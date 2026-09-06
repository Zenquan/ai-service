"""客服任务评测告警：阈值判定 + 落库 + webhook 推送。

迭代「客服任务评测体系」的告警出口。与 ``metrics`` 同款架构：抽象 + 内存回退 + MySQL。

告警规则（滑动窗口聚合 ``evaluation_events``）：
- ``handoff_rate``         转人工率：窗口内 needs_human 占比 ≥ 阈值（默认 0.5）
- ``error_rate``           出错率：窗口内 error 占比 ≥ 阈值（默认 0.3）
- ``unfounded_rate``       无依据承诺率：窗口内 citation_valid=False 占比 ≥ 阈值（默认 0.3）
- ``min_samples``          窗口内样本数下限（默认 10）：样本不足不告警，避免小样本误报

触发后：
1. 落库 ``evaluation_alerts``（MySQL / 内存回退），字段已过 PII 脱敏；
2. 若配置 ``ALERT_WEBHOOK_URL``，异步 POST 一条 JSON 通知（失败静默，不阻塞主流程）。

表结构：
- evaluation_alerts(
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    rule VARCHAR(64),
    current_value DECIMAL(10,4),
    threshold DECIMAL(10,4),
    window_samples INT,
    conversation_id VARCHAR(128),
    trace_id VARCHAR(64),
    message VARCHAR(255),
    created_at DATETIME,
    INDEX idx_created (created_at)
  )
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import pymysql

from server.services.metrics import EvaluationRecorder

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 默认阈值（可用环境变量 ALERT_HANDOFF_RATE / ALERT_ERROR_RATE / ALERT_UNFOUNDED_RATE 覆盖）。
DEFAULT_RULES: dict[str, dict[str, Any]] = {
    "handoff_rate": {
        "threshold": float(os.getenv("ALERT_HANDOFF_RATE", "0.5")),
        "label": "转人工率过高",
    },
    "error_rate": {
        "threshold": float(os.getenv("ALERT_ERROR_RATE", "0.3")),
        "label": "出错率过高",
    },
    "unfounded_rate": {
        "threshold": float(os.getenv("ALERT_UNFOUNDED_RATE", "0.3")),
        "label": "无依据承诺率过高",
    },
}
MIN_SAMPLES = int(os.getenv("ALERT_MIN_SAMPLES", "10"))


class AlertStore(ABC):
    """告警记录存储抽象。"""

    @abstractmethod
    def add_alert(self, alert: dict[str, Any]) -> None: ...

    @abstractmethod
    def recent(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def clear(self) -> None:
        return None


class MemoryAlertStore(AlertStore):
    def __init__(self) -> None:
        self._alerts: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add_alert(self, alert: dict[str, Any]) -> None:
        with self._lock:
            self._alerts.append(alert)
            # 内存态只保留最近 500 条，避免无限增长。
            self._alerts = self._alerts[-500:]

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._alerts[-limit:]))

    def clear(self) -> None:
        with self._lock:
            self._alerts.clear()


class MysqlAlertStore(AlertStore):
    _DDL = (
        "CREATE TABLE IF NOT EXISTS evaluation_alerts ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "rule VARCHAR(64), "
        "current_value DECIMAL(10,4), "
        "threshold DECIMAL(10,4), "
        "window_samples INT, "
        "conversation_id VARCHAR(128), "
        "trace_id VARCHAR(64), "
        "message VARCHAR(255), "
        "created_at DATETIME, "
        "INDEX idx_created (created_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    _INSERT = (
        "INSERT INTO evaluation_alerts ("
        "rule, current_value, threshold, window_samples, "
        "conversation_id, trace_id, message, created_at"
        ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    _SELECT = (
        "SELECT rule, current_value, threshold, window_samples, "
        "conversation_id, trace_id, message, created_at "
        "FROM evaluation_alerts ORDER BY id DESC LIMIT %s"
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

    def add_alert(self, alert: dict[str, Any]) -> None:
        values = (
            _truncate(alert.get("rule"), 64),
            float(alert.get("current_value") or 0.0),
            float(alert.get("threshold") or 0.0),
            int(alert.get("window_samples") or 0),
            _truncate(alert.get("conversation_id"), 128),
            _truncate(alert.get("trace_id"), 64),
            _truncate(alert.get("message"), 255),
            alert.get("created_at") or _now(),
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._INSERT, values)
            conn.commit()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._SELECT, (limit,))
                rows = cur.fetchall()
        return [
            {
                "rule": r[0],
                "current_value": float(r[1]) if r[1] is not None else 0.0,
                "threshold": float(r[2]) if r[2] is not None else 0.0,
                "window_samples": r[3],
                "conversation_id": r[4],
                "trace_id": r[5],
                "message": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


class AlertEvaluator:
    """从 EvaluationRecorder 的事件窗口计算比率并触发告警。

    - ``check()``：对最新窗口做一次阈值判定，命中则落库 + webhook。
    - 幂等性交给调用方（每轮对话结束后调一次，基于当前累计事件窗口）。
    """

    def __init__(self, recorder: EvaluationRecorder, store: AlertStore) -> None:
        self._recorder = recorder
        self._store = store
        self._webhook_url = os.getenv("ALERT_WEBHOOK_URL", "").strip()

    def _window(self) -> list[dict[str, Any]]:
        # 取内存 recorder 的全部事件（生产 MySQL 下事件在 DB，窗口聚合应改 SQL；
        # 此处聚焦进程内实时告警，覆盖默认内存回退路径）。
        getter = getattr(self._recorder, "events", None)
        if callable(getter):
            return getter()
        if isinstance(getter, list):
            return getter
        return []

    def check(self) -> list[dict[str, Any]]:
        """对当前窗口做阈值判定，返回本次新触发的告警列表（含已落库的 dict）。"""
        window = self._window()
        total = len(window)
        if total < MIN_SAMPLES:
            return []

        fired: list[dict[str, Any]] = []
        # 统计各指标命中数
        handoff = sum(1 for e in window if bool(e.get("needs_human")))
        errors = sum(1 for e in window if bool(e.get("error")))
        unfounded = sum(1 for e in window if e.get("citation_valid") is False)

        metrics = {
            "handoff_rate": handoff / total,
            "error_rate": errors / total,
            "unfounded_rate": unfounded / total,
        }
        for rule, value in metrics.items():
            cfg = DEFAULT_RULES[rule]
            if value >= cfg["threshold"]:
                alert = self._build_alert(rule, value, cfg, total)
                self._store.add_alert(alert)
                self._notify(alert)
                fired.append(alert)

        return fired

    def _build_alert(
        self,
        rule: str,
        value: float,
        cfg: dict[str, Any],
        total: int,
    ) -> dict[str, Any]:
        last = self._window()[-1] if self._window() else {}
        return {
            "rule": rule,
            "current_value": round(value, 4),
            "threshold": float(cfg["threshold"]),
            "window_samples": total,
            "conversation_id": last.get("conversation_id"),
            "trace_id": last.get("trace_id"),
            "message": f"{cfg['label']}：{value:.1%} ≥ 阈值 {cfg['threshold']:.0%}（窗口 {total} 条）",
            "created_at": _now(),
        }

    def _notify(self, alert: dict[str, Any]) -> None:
        if not self._webhook_url:
            return
        payload = json.dumps(
            {
                "type": "customer_service_alert",
                "alert": alert,
                "fired_at": _iso_now(),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            req = urllib.request.Request(
                self._webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # 独立线程发送，避免 webhook 抖动拖慢主流程。
            threading.Thread(target=self._send, args=(req,), daemon=True).start()
        except Exception:  # noqa: BLE001
            logger.debug("alert webhook schedule failed", exc_info=True)

    def _send(self, req: urllib.request.Request) -> None:
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status >= 400:
                    logger.warning("alert webhook returned %s", resp.status)
        except Exception as exc:  # noqa: BLE001 —— webhook 失败不阻塞业务
            logger.debug("alert webhook failed: %s", exc)


def build_default_alert_store() -> AlertStore:
    """按 MYSQL_* 配置构造告警存储；无 MySQL 或建表失败回退内存。"""
    host = os.getenv("MYSQL_HOST", "").strip()
    if not host:
        return MemoryAlertStore()
    try:
        store: AlertStore = MysqlAlertStore(
            host=host,
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "rag"),
        )
        store.ensure_schema()
        return store
    except Exception as exc:  # noqa: BLE001
        logger.warning("AlertStore: MySQL 不可用，回退内存（%s）", exc)
        return MemoryAlertStore()


__all__ = [
    "AlertStore",
    "MemoryAlertStore",
    "MysqlAlertStore",
    "AlertEvaluator",
    "build_default_alert_store",
    "DEFAULT_RULES",
    "MIN_SAMPLES",
]
