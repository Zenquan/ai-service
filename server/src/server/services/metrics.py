"""客服任务评测指标埋点：MySQL 持久化 + 内存回退。

设计要点（迭代「客服任务评测体系」）：
- 与 ``chat_store`` 同款架构：抽象基类 + 内存实现 + MySQL 实现，
  首用幂等建表，无 MySQL 配置或连接失败时自动回退内存。
- 每轮对话结束后由 ``customer_service`` 在图结果产出位置调用
  ``record_event``，写入 ``evaluation_events`` 表。
- 字段中可能含 PII 的（``content`` / ``clarify_reason`` / ``handoff_reason`` 等）
  在写入前由 ``server.services.redactor.redact_metrics`` 白名单脱敏，
  指标元数据（``intent`` / ``response_mode`` / ``latency_ms`` 等）原样保留。
- 评测聚合层（``metrics_aggregator``）后续从这张表查询窗口聚合指标。

表结构：
- evaluation_events(
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    conversation_id VARCHAR(128),
    message_id VARCHAR(64),
    trace_id VARCHAR(64),
    intent VARCHAR(32),
    response_mode VARCHAR(32),
    citation_valid TINYINT(1),
    needs_human TINYINT(1),
    needs_clarification TINYINT(1),
    tool_status VARCHAR(32),
    tool_calls_count INT,
    materials_count INT,
    citations_count INT,
    error VARCHAR(255),
    latency_ms INT,
    created_at DATETIME,
    INDEX idx_created (created_at),
    INDEX idx_conversation (conversation_id)
  )
"""
from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import pymysql

from server.services.redactor import redact_metrics

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class EvaluationRecorder(ABC):
    """抽象基类：record_event 接受通用 dict payload。"""

    @abstractmethod
    def record_event(self, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def summary(self) -> dict[str, Any]:
        """聚合出评测指标的 JSON 摘要，供运营端「性能」看板消费。

        返回结构（Memory / MySQL 同构）：
        - ``storage``：来源（memory / mysql）
        - ``total`` / ``resolved`` / ``handoff`` / ``clarification`` / ``errors``：总量与分类计数
        - ``tool_success`` / ``tool_failure``：工具调用成功 / 失败计数（``tool_status`` 为 ok 记成功，
          error / timeout 等记失败，none 不计入）
        - ``citation_valid`` / ``citation_invalid``：引用校验有效 / 无效计数
        - ``latency_avg_ms`` / ``latency_p50_ms`` / ``latency_p95_ms``：响应时延统计（ms）
        - ``by_intent`` / ``by_response_mode`` / ``by_tool_status``：维度分布
        """

    def clear(self) -> None:  # 内存实现需要；MySQL 保持 no-op
        return None


def _percentile(values: list[float], p: float) -> float | None:
    """线性插值分位数（p ∈ [0, 1]）。空序列返回 None，单元素直接返回。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    rank = (len(ordered) - 1) * p
    lower = int(rank)
    upper = lower + 1
    if upper >= len(ordered):
        return round(ordered[-1], 1)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 1)


class MemoryEvaluationRecorder(EvaluationRecorder):
    """测试 / 进程内回退实现。"""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record_event(self, payload: dict[str, Any]) -> None:
        clean = redact_metrics({**payload, "created_at": _now()})
        with self._lock:
            self._events.append(clean)

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def aggregate(self, since: datetime | None = None) -> dict[str, Any]:
        """简单的窗口聚合：返回每种 intent / response_mode / tool_status 的计数。"""
        items = self.events
        if since is not None:
            items = [e for e in items if e.get("created_at", "") >= since.strftime("%Y-%m-%d %H:%M:%S")]
        result: dict[str, dict[str, int]] = {"intent": {}, "response_mode": {}, "tool_status": {}}
        for e in items:
            for bucket, key in (("intent", "intent"), ("response_mode", "response_mode"), ("tool_status", "tool_status")):
                value = str(e.get(key) or "unknown")
                result[bucket][value] = result[bucket].get(value, 0) + 1
        return result

    def summary(self) -> dict[str, Any]:
        """从内存事件列表聚合摘要（与 MySQL 实现同构，见基类 docstring）。"""
        return _summarize_events(self.events, storage="memory")


class MysqlEvaluationRecorder(EvaluationRecorder):
    """MySQL 实现：pymysql 短连接、首用幂等建表。"""

    _DDL = (
        "CREATE TABLE IF NOT EXISTS evaluation_events ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "conversation_id VARCHAR(128), "
        "message_id VARCHAR(64), "
        "trace_id VARCHAR(64), "
        "intent VARCHAR(32), "
        "response_mode VARCHAR(32), "
        "citation_valid TINYINT(1), "
        "needs_human TINYINT(1), "
        "needs_clarification TINYINT(1), "
        "tool_status VARCHAR(32), "
        "tool_calls_count INT, "
        "materials_count INT, "
        "citations_count INT, "
        "error VARCHAR(255), "
        "latency_ms INT, "
        "created_at DATETIME, "
        "INDEX idx_created (created_at), "
        "INDEX idx_conversation (conversation_id)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )

    _INSERT = (
        "INSERT INTO evaluation_events ("
        "conversation_id, message_id, trace_id, intent, response_mode, "
        "citation_valid, needs_human, needs_clarification, tool_status, "
        "tool_calls_count, materials_count, citations_count, error, latency_ms, created_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self._conn_kwargs = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": "utf8mb4",
            "connect_timeout": 3,
            "read_timeout": 3,
            "write_timeout": 3,
        }

    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(**self._conn_kwargs)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._DDL)
            conn.commit()

    def record_event(self, payload: dict[str, Any]) -> None:
        clean = redact_metrics({**payload, "created_at": payload.get("created_at") or _now()})
        values = (
            _truncate(clean.get("conversation_id"), 128),
            _truncate(clean.get("message_id"), 64),
            _truncate(clean.get("trace_id"), 64),
            _truncate(clean.get("intent"), 32),
            _truncate(clean.get("response_mode"), 32),
            int(bool(clean.get("citation_valid"))),
            int(bool(clean.get("needs_human"))),
            int(bool(clean.get("needs_clarification"))),
            _truncate(clean.get("tool_status"), 32),
            int(clean.get("tool_calls_count") or 0),
            int(clean.get("materials_count") or 0),
            int(clean.get("citations_count") or 0),
            _truncate(clean.get("error"), 255),
            int(clean.get("latency_ms") or 0),
            clean["created_at"],
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._INSERT, values)
            conn.commit()

    _SUMMARY_AGG = (
        "SELECT "
        "COUNT(*), "
        "SUM(needs_human), "
        "SUM(needs_clarification), "
        "SUM(CASE WHEN error IS NOT NULL AND error <> '' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN tool_status = 'ok' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN tool_status IN ('error', 'timeout') THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN citation_valid = 1 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN citation_valid = 0 THEN 1 ELSE 0 END), "
        "AVG(latency_ms) "
        "FROM evaluation_events"
    )
    _SUMMARY_GROUP_BY = "SELECT {col}, COUNT(*) FROM evaluation_events GROUP BY {col}"

    def summary(self) -> dict[str, Any]:
        """用 SQL 聚合出摘要（分位数在 Python 侧计算，与内存实现同构）。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._SUMMARY_AGG)
                row = cur.fetchone()
            if row is None or row[0] == 0:
                return _summarize_events([], storage="mysql")
            total, handoff, clarification, errors, tool_ok, tool_fail, cit_ok, cit_bad, avg_ms = row
            handoff = handoff or 0
            clarification = clarification or 0
            errors = errors or 0
            tool_ok = tool_ok or 0
            tool_fail = tool_fail or 0
            cit_ok = cit_ok or 0
            cit_bad = cit_bad or 0
            avg_ms = round(float(avg_ms), 1) if avg_ms is not None else None

            groups: dict[str, dict[str, int]] = {}
            for col in ("intent", "response_mode", "tool_status"):
                with conn.cursor() as cur:
                    cur.execute(self._SUMMARY_GROUP_BY.format(col=col))
                    groups[col] = {str(k) or "unknown": int(v) for k, v in cur.fetchall()}

            with conn.cursor() as cur:
                cur.execute("SELECT latency_ms FROM evaluation_events WHERE latency_ms IS NOT NULL ORDER BY latency_ms")
                latencies = [float(r[0]) for r in cur.fetchall()]

        return {
            "storage": "mysql",
            "total": int(total),
            "resolved": int(total) - int(handoff) - int(clarification) - int(errors),
            "handoff": int(handoff),
            "clarification": int(clarification),
            "errors": int(errors),
            "tool_success": int(tool_ok),
            "tool_failure": int(tool_fail),
            "citation_valid": int(cit_ok),
            "citation_invalid": int(cit_bad),
            "latency_avg_ms": avg_ms,
            "latency_p50_ms": _percentile(latencies, 0.5),
            "latency_p95_ms": _percentile(latencies, 0.95),
            "by_intent": groups["intent"],
            "by_response_mode": groups["response_mode"],
            "by_tool_status": groups["tool_status"],
        }


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


def _summarize_events(events: list[dict[str, Any]], storage: str) -> dict[str, Any]:
    """从事件列表聚合出性能摘要（Memory 与 MySQL 拉取后的统一出口）。

    ``tool_status`` 语义：``ok`` 记成功，``error`` / ``timeout`` 记失败，
    ``none`` / 空 不参与成功率；``citation_valid`` 为 None 不参与有效/无效计数。
    """
    total = len(events)
    resolved = handoff = clarification = errors = 0
    tool_success = tool_failure = 0
    citation_valid = citation_invalid = 0
    latencies: list[float] = []
    by_intent: dict[str, int] = {}
    by_response_mode: dict[str, int] = {}
    by_tool_status: dict[str, int] = {}

    for e in events:
        if bool(e.get("needs_human")):
            handoff += 1
        if bool(e.get("needs_clarification")):
            clarification += 1
        if bool(e.get("error")):
            errors += 1
        if not (bool(e.get("needs_human")) or bool(e.get("needs_clarification")) or bool(e.get("error"))):
            resolved += 1

        status = e.get("tool_status")
        if status == "ok":
            tool_success += 1
        elif status in ("error", "timeout"):
            tool_failure += 1

        cv = e.get("citation_valid")
        if cv is True:
            citation_valid += 1
        elif cv is False:
            citation_invalid += 1

        ms = e.get("latency_ms")
        if ms is not None:
            try:
                latencies.append(float(ms))
            except (TypeError, ValueError):
                pass

        _inc(by_intent, str(e.get("intent") or "unknown"))
        _inc(by_response_mode, str(e.get("response_mode") or "unknown"))
        _inc(by_tool_status, str(status or "none"))

    avg_ms = round(sum(latencies) / len(latencies), 1) if latencies else None
    return {
        "storage": storage,
        "total": total,
        "resolved": resolved,
        "handoff": handoff,
        "clarification": clarification,
        "errors": errors,
        "tool_success": tool_success,
        "tool_failure": tool_failure,
        "citation_valid": citation_valid,
        "citation_invalid": citation_invalid,
        "latency_avg_ms": avg_ms,
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "by_intent": by_intent,
        "by_response_mode": by_response_mode,
        "by_tool_status": by_tool_status,
    }


def _inc(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def build_default_recorder() -> EvaluationRecorder:
    """根据环境变量构造：MYSQL_HOST 存在且能建表 → MySQL；否则内存。

    任何配置错误（连接失败 / 权限不足）都安静回退内存，不阻塞业务。
    """
    host = os.getenv("MYSQL_HOST", "").strip()
    if not host:
        return MemoryEvaluationRecorder()
    try:
        recorder: EvaluationRecorder = MysqlEvaluationRecorder(
            host=host,
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "rag"),
        )
        recorder.ensure_schema()
        logger.info("EvaluationRecorder: MySQL (%s)", host)
        return recorder
    except Exception as exc:  # noqa: BLE001 —— 评测指标回退不应阻塞主流程
        logger.warning("EvaluationRecorder: MySQL 不可用，回退内存（%s）", exc)
        return MemoryEvaluationRecorder()


__all__ = [
    "EvaluationRecorder",
    "MemoryEvaluationRecorder",
    "MysqlEvaluationRecorder",
    "build_default_recorder",
]