"""LangGraph checkpoint 生产持久化：MySQL 后端 checkpointer。

客服图（customer_service）的多轮状态——澄清/转人工中间态——由 LangGraph 原生
checkpointer 持久化到 MySQL，服务重启后可基于 ``thread_id``（= conversation_id）恢复。

实现要点：
- 继承 ``langgraph.checkpoint.base.BaseCheckpointSaver``，落两张表
  ``langgraph_checkpoints`` / ``langgraph_checkpoint_writes``，首用幂等建表。
- 序列化复用 LangGraph 内置 ``JsonPlusSerializer``（typed，实际走 msgpack），
  checkpoint / metadata / writes 均以 BLOB 落库，可无损往返 langchain Message 等复杂类型。
- pymysql 短连接（单操作单连接 + 短超时），适配云托管 MinNum=0 缩容场景。
- 无 ``MYSQL_HOST`` 或建表失败时由 :func:`build_checkpointer` 回退 ``InMemorySaver``
  （仅进程内，重启不恢复），与 chat_store 的 memory/mysql 回退语义对齐。
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import InMemorySaver

logger = logging.getLogger(__name__)

_CHECKPOINT_TABLE = "langgraph_checkpoints"
_WRITES_TABLE = "langgraph_checkpoint_writes"

_CREATE_CHECKPOINTS = f"""
CREATE TABLE IF NOT EXISTS {_CHECKPOINT_TABLE} (
    thread_id            VARCHAR(191) NOT NULL,
    checkpoint_ns        VARCHAR(191) NOT NULL DEFAULT '',
    checkpoint_id        VARCHAR(191) NOT NULL,
    parent_checkpoint_id VARCHAR(191) NULL,
    checkpoint_type      VARCHAR(32)  NOT NULL,
    checkpoint           MEDIUMBLOB   NOT NULL,
    metadata_type        VARCHAR(32)  NOT NULL,
    metadata             MEDIUMBLOB   NOT NULL,
    created_at           DATETIME(3)  NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_WRITES = f"""
CREATE TABLE IF NOT EXISTS {_WRITES_TABLE} (
    thread_id       VARCHAR(191) NOT NULL,
    checkpoint_ns   VARCHAR(191) NOT NULL DEFAULT '',
    checkpoint_id   VARCHAR(191) NOT NULL,
    task_id         VARCHAR(191) NOT NULL,
    idx             INT          NOT NULL,
    channel         VARCHAR(191) NOT NULL,
    value_type      VARCHAR(32)  NULL,
    value           MEDIUMBLOB   NULL,
    task_path       VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _utcnow():
    """带毫秒精度的 UTC 时间（对应 DATETIME(3)）。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


class MysqlCheckpointSaver(BaseCheckpointSaver):
    """LangGraph checkpointer 的 MySQL 实现（同步接口 + 薄 async 包装）。"""

    def __init__(self) -> None:
        super().__init__()
        host = os.environ.get("MYSQL_HOST")
        if not host:
            raise ValueError("MYSQL_HOST 未配置")
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
        self._tables_ready = False

    def _connect(self):
        import pymysql

        return pymysql.connect(**self._config)

    def setup(self) -> None:
        """幂等建表；失败抛 RuntimeError，由 build_checkpointer 决定回退。"""
        if self._tables_ready:
            return
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(_CREATE_CHECKPOINTS)
                    cursor.execute(_CREATE_WRITES)
        except Exception as exc:  # noqa: BLE001 —— pymysql.MySQLError 及其子类
            raise RuntimeError(f"LangGraph checkpoint 建表失败: {exc}") from exc
        self._tables_ready = True

    # ── 内部读写 ──────────────────────────────────────────────
    def _fetch_checkpoint(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> dict | None:
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        f"SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                        f"checkpoint_type, checkpoint, metadata_type, metadata "
                        f"FROM {_CHECKPOINT_TABLE} "
                        f"WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s",
                        (thread_id, checkpoint_ns, checkpoint_id),
                    )
                    return cursor.fetchone()
        except pymysql.MySQLError as exc:
            logger.error("checkpoint 读取失败 thread_id=%s: %s", thread_id, exc)
            return None

    def _fetch_latest_checkpoint(self, thread_id: str, checkpoint_ns: str) -> dict | None:
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        f"SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                        f"checkpoint_type, checkpoint, metadata_type, metadata "
                        f"FROM {_CHECKPOINT_TABLE} "
                        f"WHERE thread_id = %s AND checkpoint_ns = %s "
                        f"ORDER BY checkpoint_id DESC LIMIT 1",
                        (thread_id, checkpoint_ns),
                    )
                    return cursor.fetchone()
        except pymysql.MySQLError as exc:
            logger.error("checkpoint 读取失败 thread_id=%s: %s", thread_id, exc)
            return None

    def _fetch_writes(self, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> list[dict]:
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        f"SELECT task_id, channel, value_type, value "
                        f"FROM {_WRITES_TABLE} "
                        f"WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s "
                        f"ORDER BY task_id ASC, idx ASC",
                        (thread_id, checkpoint_ns, checkpoint_id),
                    )
                    return cursor.fetchall()
        except pymysql.MySQLError as exc:
            logger.error("checkpoint writes 读取失败 thread_id=%s: %s", thread_id, exc)
            return []

    def _to_tuple(self, row: dict, config: RunnableConfig) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed((row["checkpoint_type"], row["checkpoint"]))
        metadata = self.serde.loads_typed((row["metadata_type"], row["metadata"]))
        thread_id = row["thread_id"]
        checkpoint_ns = row["checkpoint_ns"]
        checkpoint_id = row["checkpoint_id"]
        parent_id = row["parent_checkpoint_id"]
        writes = self._fetch_writes(thread_id, checkpoint_ns, checkpoint_id)
        pending_writes = [
            (w["task_id"], w["channel"], self.serde.loads_typed((w["value_type"], w["value"])))
            for w in writes
            if w["value"] is not None
        ]
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint_id}},
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": parent_id}}
                if parent_id
                else None
            ),
            pending_writes=pending_writes,
        )

    # ── BaseCheckpointSaver 同步接口 ──────────────────────────
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        self.setup()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        if checkpoint_id := get_checkpoint_id(config):
            row = self._fetch_checkpoint(thread_id, checkpoint_ns, checkpoint_id)
        else:
            row = self._fetch_latest_checkpoint(thread_id, checkpoint_ns)
        if row is None:
            return None
        return self._to_tuple(row, config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        import pymysql

        self.setup()
        thread_id = config["configurable"]["thread_id"] if config else None
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "") if config else ""
        checkpoint_id = get_checkpoint_id(config) if config else None
        before_id = get_checkpoint_id(before) if before else None

        where = []
        params: list[Any] = []
        if thread_id is not None:
            where.append("thread_id = %s")
            params.append(thread_id)
        if config is not None:
            where.append("checkpoint_ns = %s")
            params.append(checkpoint_ns)
        if checkpoint_id:
            where.append("checkpoint_id = %s")
            params.append(checkpoint_id)
        if before_id:
            where.append("checkpoint_id < %s")
            params.append(before_id)
        if limit is not None:
            params.append(int(limit))
        sql = f"SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata FROM {_CHECKPOINT_TABLE}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY checkpoint_id DESC"
        if limit is not None:
            sql += " LIMIT %s"

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except pymysql.MySQLError as exc:
            logger.error("checkpoint list 失败: %s", exc)
            return

        for row in rows:
            checkpoint = self.serde.loads_typed((row["checkpoint_type"], row["checkpoint"]))
            metadata = self.serde.loads_typed((row["metadata_type"], row["metadata"]))
            if filter and not all(
                query_value == metadata.get(query_key) for query_key, query_value in filter.items()
            ):
                continue
            yield self._to_tuple(row, config or {"configurable": {"thread_id": row["thread_id"]}})

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        import pymysql

        self.setup()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        parent_id = config["configurable"].get("checkpoint_id")
        checkpoint_type, checkpoint_bytes = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_bytes = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"INSERT INTO {_CHECKPOINT_TABLE} "
                        f"(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                        f"checkpoint_type, checkpoint, metadata_type, metadata, created_at) "
                        f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        f"ON DUPLICATE KEY UPDATE "
                        f"parent_checkpoint_id = VALUES(parent_checkpoint_id), "
                        f"checkpoint_type = VALUES(checkpoint_type), "
                        f"checkpoint = VALUES(checkpoint), "
                        f"metadata_type = VALUES(metadata_type), "
                        f"metadata = VALUES(metadata), "
                        f"created_at = VALUES(created_at)",
                        (
                            thread_id,
                            checkpoint_ns,
                            checkpoint["id"],
                            parent_id,
                            checkpoint_type,
                            checkpoint_bytes,
                            metadata_type,
                            metadata_bytes,
                            _utcnow(),
                        ),
                    )
        except pymysql.MySQLError as exc:
            logger.error("checkpoint 写入失败 thread_id=%s: %s", thread_id, exc)
            raise
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        import pymysql

        self.setup()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    for idx, (channel, value) in enumerate(writes):
                        write_idx = WRITES_IDX_MAP.get(channel, idx)
                        value_type, value_bytes = self.serde.dumps_typed(value)
                        cursor.execute(
                            f"INSERT INTO {_WRITES_TABLE} "
                            f"(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, value_type, value, task_path) "
                            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            f"ON DUPLICATE KEY UPDATE "
                            f"channel = VALUES(channel), value_type = VALUES(value_type), "
                            f"value = VALUES(value), task_path = VALUES(task_path)",
                            (
                                thread_id,
                                checkpoint_ns,
                                checkpoint_id,
                                task_id,
                                write_idx,
                                channel,
                                value_type,
                                value_bytes,
                                task_path,
                            ),
                        )
        except pymysql.MySQLError as exc:
            logger.error("checkpoint writes 写入失败 thread_id=%s: %s", thread_id, exc)
            raise

    def delete_thread(self, thread_id: str) -> None:
        import pymysql

        self.setup()
        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(f"DELETE FROM {_WRITES_TABLE} WHERE thread_id = %s", (thread_id,))
                    cursor.execute(f"DELETE FROM {_CHECKPOINT_TABLE} WHERE thread_id = %s", (thread_id,))
        except pymysql.MySQLError as exc:
            logger.error("checkpoint 删除失败 thread_id=%s: %s", thread_id, exc)
            raise

    # ── 薄 async 包装（客服图当前走同步 invoke；保留以兼容 ainvoke） ──
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        return self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        return self.delete_thread(thread_id)


def build_checkpointer() -> BaseCheckpointSaver:
    """按环境变量选择 checkpointer。

    返回 (checkpointer, is_persistent)。MYSQL_HOST 存在且建表成功 → MySQL 持久化；
    否则回退 InMemorySaver（进程内，重启不恢复），与 chat_store 的 memory 回退一致。
    """
    if os.environ.get("MYSQL_HOST"):
        try:
            saver = MysqlCheckpointSaver()
            saver.setup()
            return saver
        except Exception as exc:  # noqa: BLE001 —— 建表/连接失败回退内存
            logger.warning("MySQL checkpointer 不可用，回退内存 checkpointer（重启不恢复）: %s", exc)
    return InMemorySaver()
