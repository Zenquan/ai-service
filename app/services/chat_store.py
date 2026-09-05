"""会话存储层：MySQL 持久化 + 内存回退。

设计要点：
- ``ChatStore`` 抽象会话/消息/图状态快照的读写接口，便于测试注入。
- ``MemoryChatStore``：保留 MVP 行为，进程重启即丢（无 MYSQL_* 配置或 MySQL 不可用时的回退）。
- ``MysqlChatStore``：pymysql 短连接（每次操作独立连接 + 短超时），
  适配云托管 MinNum=0 缩容场景，故障恢复自然。
- 连接失败由 ``customer_service`` 决定回退策略，本层只抛 ``StoreUnavailable``。

表结构见云端 MySQL 库：
- conversations(id, user_id, tenant_id, status, handoff_reason, created_at, updated_at)
- messages(id, conversation_id, role, content, response_mode, citations JSON,
  materials JSON, needs_human, handoff_reason, created_at)
- graph_checkpoints(conversation_id, graph_state JSON, updated_at)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 传入 LangGraph 的历史消息窗口上限（避免无限历史撑爆上下文）。
HISTORY_LIMIT = 20


class StoreUnavailable(RuntimeError):
    """MySQL 存储不可用（连接失败/超时）。"""


def _utcnow() -> datetime:
    """带毫秒精度的 UTC 时间（对应 DATETIME(3)）。"""
    return datetime.now(timezone.utc).replace(microsecond=(datetime.now(timezone.utc).microsecond // 1000) * 1000)


def _dt_to_iso(dt: datetime) -> str:
    """数据库 DATETIME 转带时区的 ISO 字符串，与内存版 created_at 格式一致。"""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class ChatStore(ABC):
    """会话存储抽象。"""

    @abstractmethod
    def get_conversation(self, conversation_id: str) -> dict | None:
        """读取会话元数据；不存在返回 None。"""

    @abstractmethod
    def get_or_create_conversation(self, conversation_id: str) -> dict:
        """读取会话，不存在则创建。"""

    @abstractmethod
    def update_conversation(self, conversation_id: str, **fields: Any) -> dict | None:
        """更新会话元数据（status/handoff_reason/updated_at）。"""

    @abstractmethod
    def append_message(self, conversation_id: str, message: dict) -> None:
        """追加一条消息。"""

    @abstractmethod
    def list_conversations(self, limit: int = 50) -> list[dict]:
        """按更新时间倒序返回会话摘要列表（不含消息）。"""

    @abstractmethod
    def list_messages(self, conversation_id: str) -> list[dict]:
        """按时间顺序返回会话全部消息。"""

    @abstractmethod
    def save_checkpoint(self, conversation_id: str, state: dict) -> None:
        """保存图状态快照。"""

    @abstractmethod
    def load_checkpoint(self, conversation_id: str) -> dict | None:
        """读取图状态快照；不存在返回 None。"""

    @abstractmethod
    def clear(self) -> None:
        """清空全部数据（仅内存实现真正执行；MySQL 实现拒绝以防误清生产数据）。"""


class MemoryChatStore(ChatStore):
    """进程内存存储：MVP 默认行为，服务重启即丢。"""

    def __init__(self) -> None:
        self._conversations: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get_conversation(self, conversation_id: str) -> dict | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            return dict(conversation) if conversation else None

    def get_or_create_conversation(self, conversation_id: str) -> dict:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                now = _dt_to_iso(_utcnow())
                conversation = {
                    "id": conversation_id,
                    "user_id": None,
                    "tenant_id": None,
                    "status": "open",
                    "handoff_reason": None,
                    "created_at": now,
                    "updated_at": now,
                    "messages": [],
                }
                self._conversations[conversation_id] = conversation
            return dict(conversation)

    def update_conversation(self, conversation_id: str, **fields: Any) -> dict | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                return None
            for key, value in fields.items():
                conversation[key] = value
            return dict(conversation)

    def append_message(self, conversation_id: str, message: dict) -> None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            conversation["messages"].append(message)

    def list_messages(self, conversation_id: str) -> list[dict]:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            return list(conversation["messages"]) if conversation else []

    def list_conversations(self, limit: int = 50) -> list[dict]:
        with self._lock:
            conversations = sorted(
                self._conversations.values(),
                key=lambda item: item["updated_at"],
                reverse=True,
            )
            return [
                {
                    "id": item["id"],
                    "status": item["status"],
                    "handoff_reason": item["handoff_reason"],
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                    "message_count": len(item["messages"]),
                    "first_message": next(
                        (m["content"] for m in item["messages"] if m.get("role") == "user"),
                        "",
                    ),
                }
                for item in conversations[:limit]
            ]

    def save_checkpoint(self, conversation_id: str, state: dict) -> None:
        # 内存版不保留快照：每轮通过 messages 重建上下文。
        return None

    def load_checkpoint(self, conversation_id: str) -> dict | None:
        return None

    def clear(self) -> None:
        with self._lock:
            self._conversations.clear()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class MysqlChatStore(ChatStore):
    """MySQL 会话存储：pymysql 短连接，单操作单连接。"""

    def __init__(self) -> None:
        host = os.environ.get("MYSQL_HOST")
        if not host:
            raise StoreUnavailable("MYSQL_HOST 未配置")
        self._config = {
            "host": host,
            "port": _env_int("MYSQL_PORT", 3306),
            "user": os.environ.get("MYSQL_USER", "zenquan"),
            "password": os.environ.get("MYSQL_PASSWORD", ""),
            "database": os.environ.get("MYSQL_DB", ""),
            "charset": "utf8mb4",
            "autocommit": True,
            "connect_timeout": _env_int("MYSQL_CONNECT_TIMEOUT", 3),
            "read_timeout": _env_int("MYSQL_READ_TIMEOUT", 5),
            "write_timeout": _env_int("MYSQL_WRITE_TIMEOUT", 5),
        }

    def _connect(self):
        import pymysql

        return pymysql.connect(**self._config)

    def get_conversation(self, conversation_id: str) -> dict | None:
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT id, user_id, tenant_id, status, handoff_reason, created_at, updated_at "
                        "FROM conversations WHERE id = %s",
                        (conversation_id,),
                    )
                    row = cursor.fetchone()
        except pymysql.MySQLError as exc:
            raise StoreUnavailable(f"MySQL 读取会话失败: {exc}") from exc
        if row is None:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "tenant_id": row["tenant_id"],
            "status": row["status"],
            "handoff_reason": row["handoff_reason"],
            "created_at": _dt_to_iso(row["created_at"]),
            "updated_at": _dt_to_iso(row["updated_at"]),
        }

    def get_or_create_conversation(self, conversation_id: str) -> dict:
        existing = self.get_conversation(conversation_id)
        if existing:
            return existing
        now = _utcnow()
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO conversations (id, status, handoff_reason, created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (conversation_id, "open", None, now, now),
                    )
        except Exception as exc:  # noqa: BLE001 — pymysql.MySQLError 及其子类
            raise StoreUnavailable(f"MySQL 创建会话失败: {exc}") from exc
        return self.get_conversation(conversation_id)  # type: ignore[return-value]

    def update_conversation(self, conversation_id: str, **fields: Any) -> dict | None:
        allowed = {"status", "handoff_reason", "updated_at"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_conversation(conversation_id)
        if "updated_at" in updates and isinstance(updates["updated_at"], str):
            updates["updated_at"] = datetime.fromisoformat(updates["updated_at"])
        sets = ", ".join(f"{key} = %s" for key in updates)
        values = list(updates.values()) + [conversation_id]
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(f"UPDATE conversations SET {sets} WHERE id = %s", values)
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"MySQL 更新会话失败: {exc}") from exc
        return self.get_conversation(conversation_id)

    def append_message(self, conversation_id: str, message: dict) -> None:
        created_at = message.get("created_at") or _dt_to_iso(_utcnow())
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        citations = message.get("citations")
        materials = message.get("materials")
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO messages "
                        "(id, conversation_id, role, content, response_mode, citations, materials, "
                        "needs_human, handoff_reason, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            message["id"],
                            conversation_id,
                            message["role"],
                            message["content"],
                            message.get("response_mode"),
                            json.dumps(citations) if citations is not None else None,
                            json.dumps(materials) if materials is not None else None,
                            int(bool(message.get("needs_human", False))),
                            message.get("handoff_reason"),
                            created_at,
                        ),
                    )
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"MySQL 写入消息失败: {exc}") from exc

    def list_messages(self, conversation_id: str) -> list[dict]:
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT id, role, content, response_mode, citations, materials, "
                        "needs_human, handoff_reason, created_at "
                        "FROM messages WHERE conversation_id = %s ORDER BY created_at ASC, id ASC",
                        (conversation_id,),
                    )
                    rows = cursor.fetchall()
        except pymysql.MySQLError as exc:
            raise StoreUnavailable(f"MySQL 读取消息失败: {exc}") from exc
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": _dt_to_iso(row["created_at"]),
                "response_mode": row["response_mode"],
                "citations": json.loads(row["citations"]) if row["citations"] is not None else None,
                "materials": json.loads(row["materials"]) if row["materials"] is not None else None,
                "needs_human": bool(row["needs_human"]),
                "handoff_reason": row["handoff_reason"],
            }
            for row in rows
        ]

    def list_conversations(self, limit: int = 50) -> list[dict]:
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT c.id, c.status, c.handoff_reason, c.created_at, c.updated_at, "
                        "COUNT(m.id) AS message_count, "
                        "(SELECT m2.content FROM messages m2 "
                        " WHERE m2.conversation_id = c.id AND m2.role = 'user' "
                        " ORDER BY m2.created_at ASC, m2.id ASC LIMIT 1) AS first_message "
                        "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
                        "GROUP BY c.id, c.status, c.handoff_reason, c.created_at, c.updated_at "
                        "ORDER BY c.updated_at DESC LIMIT %s",
                        (int(limit),),
                    )
                    rows = cursor.fetchall()
        except pymysql.MySQLError as exc:
            raise StoreUnavailable(f"MySQL 读取会话列表失败: {exc}") from exc
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "handoff_reason": row["handoff_reason"],
                "created_at": _dt_to_iso(row["created_at"]),
                "updated_at": _dt_to_iso(row["updated_at"]),
                "message_count": int(row["message_count"]),
                "first_message": row.get("first_message") or "",
            }
            for row in rows
        ]

    def save_checkpoint(self, conversation_id: str, state: dict) -> None:
        now = _utcnow()
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO graph_checkpoints (conversation_id, graph_state, updated_at) "
                        "VALUES (%s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE graph_state = VALUES(graph_state), updated_at = VALUES(updated_at)",
                        (conversation_id, json.dumps(state), now),
                    )
        except Exception as exc:  # noqa: BLE001
            raise StoreUnavailable(f"MySQL 保存图状态失败: {exc}") from exc

    def load_checkpoint(self, conversation_id: str) -> dict | None:
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT graph_state FROM graph_checkpoints WHERE conversation_id = %s",
                        (conversation_id,),
                    )
                    row = cursor.fetchone()
        except pymysql.MySQLError as exc:
            raise StoreUnavailable(f"MySQL 读取图状态失败: {exc}") from exc
        if row is None:
            return None
        try:
            return json.loads(row["graph_state"])
        except (TypeError, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        # 生产数据不可误清；如需清理请使用管理端 SQL。
        raise StoreUnavailable("MySQL 存储不允许整库清空")


def build_default_store() -> tuple[ChatStore, str]:
    """按环境变量选择存储。

    返回 (store, storage_mode)。MYSQL_HOST 存在时选 MySQL；
    初始化失败回退内存（storage_mode 标记为 "memory_fallback"）。
    """
    if os.environ.get("MYSQL_HOST"):
        try:
            return MysqlChatStore(), "mysql"
        except StoreUnavailable as exc:
            logger.warning("MySQL 不可用，回退内存存储: %s", exc)
            return MemoryChatStore(), "memory_fallback"
    return MemoryChatStore(), "memory"
