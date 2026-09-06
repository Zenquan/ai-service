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

    def clear(self) -> None:  # 内存实现需要；MySQL 保持 no-op
        return None


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


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


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