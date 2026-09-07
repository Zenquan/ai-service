"""审计日志：客服全链路关键事件落库（MySQL）+ 内存回退。

设计要点（迭代「限流、熔断与审计日志」P0）：
- 记录一轮客服对话的关键事件（消息进入 / 意图分类 / 工具调用 / 转人工 / 澄清 /
  回复 / 限流拒绝 / 熔断 / 人工回复 / 异常），用 ``trace_id`` 串起同一次 HTTP 请求。
- 复用项目「抽象 + 内存回退 + MySQL 短连接 + 幂等建表」三段式范式；
  MySQL 不可用时回退内存（仅测试/本地开发，重启即丢）。
- 可能含 PII 的字段（``detail`` / ``handoff_reason`` 等）写入前经
  ``server.services.redactor.redact`` 脱敏，其余元数据字段原样保留。
- 审计写入失败静默——绝不阻塞客服主流程。

表结构：
- audit_logs(
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trace_id VARCHAR(64),
    conversation_id VARCHAR(128),
    message_id VARCHAR(64),
    user_id VARCHAR(64),
    tenant_id VARCHAR(64),
    action VARCHAR(32),
    intent VARCHAR(32),
    response_mode VARCHAR(32),
    tool_name VARCHAR(64),
    tool_status VARCHAR(32),
    needs_human TINYINT(1),
    handoff_reason VARCHAR(255),
    detail VARCHAR(1024),
    latency_ms INT,
    created_at DATETIME,
    INDEX idx_trace (trace_id),
    INDEX idx_conversation (conversation_id),
    INDEX idx_action (action),
    INDEX idx_created (created_at)
  )
"""
from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from server.services.redactor import redact

logger = logging.getLogger(__name__)

# 审计字段中可能含 PII 的白名单（写入前递归脱敏）。
_AUDIT_PII_FIELDS = frozenset({
    "detail",
    "handoff_reason",
    "content",
    "message",
    "user_message",
    "answer",
    "clarify_reason",
})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


class AuditLogger(ABC):
    """审计日志抽象：``log(action, **fields)`` 追加一条审计记录。"""

    @abstractmethod
    def log(self, action: str, **fields: Any) -> None: ...

    def clear(self) -> None:
        return None


class MemoryAuditLogger(AuditLogger):
    """内存实现（测试 / 本地无 MySQL 回退）。"""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def log(self, action: str, **fields: Any) -> None:
        entry = _redact_fields({"action": action, "created_at": _now(), **fields})
        with self._lock:
            self._entries.append(entry)
            # 内存态只保留最近 1000 条，避免无限增长。
            self._entries = self._entries[-1000:]

    @property
    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class MysqlAuditLogger(AuditLogger):
    """MySQL 实现：pymysql 短连接、首用幂等建表。"""

    _DDL = (
        "CREATE TABLE IF NOT EXISTS audit_logs ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "trace_id VARCHAR(64), "
        "conversation_id VARCHAR(128), "
        "message_id VARCHAR(64), "
        "user_id VARCHAR(64), "
        "tenant_id VARCHAR(64), "
        "action VARCHAR(32), "
        "intent VARCHAR(32), "
        "response_mode VARCHAR(32), "
        "tool_name VARCHAR(64), "
        "tool_status VARCHAR(32), "
        "needs_human TINYINT(1), "
        "handoff_reason VARCHAR(255), "
        "detail VARCHAR(1024), "
        "latency_ms INT, "
        "created_at DATETIME, "
        "INDEX idx_trace (trace_id), "
        "INDEX idx_conversation (conversation_id), "
        "INDEX idx_action (action), "
        "INDEX idx_created (created_at)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    _INSERT = (
        "INSERT INTO audit_logs ("
        "trace_id, conversation_id, message_id, user_id, tenant_id, action, intent, "
        "response_mode, tool_name, tool_status, needs_human, handoff_reason, detail, "
        "latency_ms, created_at"
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

    def _connect(self):
        import pymysql

        return pymysql.connect(**self._conn_kwargs)

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._DDL)
            conn.commit()

    def log(self, action: str, **fields: Any) -> None:
        from server.observability import get_trace_id

        clean = _redact_fields(fields)
        trace_id = clean.get("trace_id") or get_trace_id()
        values = (
            _truncate(trace_id if trace_id != "-" else None, 64),
            _truncate(clean.get("conversation_id"), 128),
            _truncate(clean.get("message_id"), 64),
            _truncate(clean.get("user_id"), 64),
            _truncate(clean.get("tenant_id"), 64),
            _truncate(action, 32),
            _truncate(clean.get("intent"), 32),
            _truncate(clean.get("response_mode"), 32),
            _truncate(clean.get("tool_name"), 64),
            _truncate(clean.get("tool_status"), 32),
            int(bool(clean.get("needs_human"))),
            _truncate(clean.get("handoff_reason"), 255),
            _truncate(clean.get("detail"), 1024),
            int(clean.get("latency_ms") or 0),
            clean.get("created_at") or _now(),
        )
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(self._INSERT, values)
                conn.commit()
        except Exception as exc:  # noqa: BLE001 —— 审计失败静默，不阻塞业务
            logger.debug("audit log write skipped: %s", exc)


def _redact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """只对 ``_AUDIT_PII_FIELDS`` 白名单字段递归脱敏，其余原样保留。"""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _AUDIT_PII_FIELDS:
            out[key] = redact(value)
        else:
            out[key] = value
    return out


def build_default_audit_logger() -> AuditLogger:
    """按环境变量构造审计器；无 MySQL 或建表失败回退内存。"""
    host = os.getenv("MYSQL_HOST", "").strip()
    if not host:
        return MemoryAuditLogger()
    try:
        logger_impl: AuditLogger = MysqlAuditLogger(
            host=host,
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE") or os.getenv("MYSQL_DB") or "rag",
        )
        logger_impl.ensure_schema()  # type: ignore[attr-defined]
        logging.getLogger(__name__).info("AuditLogger: MySQL (%s)", host)
        return logger_impl
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("AuditLogger: MySQL 不可用，回退内存（%s）", exc)
        return MemoryAuditLogger()


_default_logger: AuditLogger | None = None
_default_logger_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    """进程内单例审计器（中间件与客服服务共享）。"""
    global _default_logger
    if _default_logger is None:
        with _default_logger_lock:
            if _default_logger is None:
                _default_logger = build_default_audit_logger()
    return _default_logger


__all__ = [
    "AuditLogger",
    "MemoryAuditLogger",
    "MysqlAuditLogger",
    "build_default_audit_logger",
    "get_audit_logger",
]
