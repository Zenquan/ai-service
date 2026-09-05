"""RAG 文档切块云存储：MySQL 持久化（部署后重建向量索引的数据源）。

设计要点：
- 文档解析切块后（向量化前）的 chunks 结构存 MySQL rag_documents 表，
  部署重建镜像后 Qdrant 本地集合丢失时，从 MySQL 读 chunks 重新向量化重建。
- 连接参数与 chat_store 一致（MYSQL_* 环境变量），失败抛 StoreUnavailable 由调用方降级
  （本地开发无 MySQL 时跳过云持久化，功能不受影响）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class DocStoreUnavailable(RuntimeError):
    """文档云存储不可用（连接失败/超时）。"""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class DocStore:
    """文档切块云存储：pymysql 短连接，单操作单连接。"""

    def __init__(self) -> None:
        host = os.environ.get("MYSQL_HOST")
        if not host:
            raise DocStoreUnavailable("MYSQL_HOST 未配置")
        self._config = {
            "host": host,
            "port": _env_int("MYSQL_PORT", 3306),
            "user": os.environ.get("MYSQL_USER", "zenquan"),
            "password": os.environ.get("MYSQL_PASSWORD", ""),
            "database": os.environ.get("MYSQL_DB", ""),
            "charset": "utf8mb4",
            "autocommit": True,
            "connect_timeout": _env_int("MYSQL_CONNECT_TIMEOUT", 3),
            "read_timeout": _env_int("MYSQL_READ_TIMEOUT", 10),
            "write_timeout": _env_int("MYSQL_WRITE_TIMEOUT", 10),
        }

    def _connect(self):
        import pymysql

        return pymysql.connect(**self._config)

    def save_doc(self, doc_name: str, chunks: list[dict]) -> None:
        """保存/覆盖一份文档的切块（upsert）。"""
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO rag_documents (doc_name, chunks, updated_at) "
                        "VALUES (%s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE chunks = VALUES(chunks), updated_at = VALUES(updated_at)",
                        (doc_name, json.dumps(chunks, ensure_ascii=False), _utcnow()),
                    )
        except pymysql.MySQLError as exc:
            raise DocStoreUnavailable(f"MySQL 保存文档切块失败: {exc}") from exc

    def delete_doc(self, doc_name: str) -> None:
        """删除一份文档的切块记录（不存在时静默）。"""
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM rag_documents WHERE doc_name = %s", (doc_name,))
        except pymysql.MySQLError as exc:
            raise DocStoreUnavailable(f"MySQL 删除文档切块失败: {exc}") from exc

    def list_docs(self) -> list[dict]:
        """列出所有文档及其切块。"""
        import pymysql

        try:
            with self._connect() as conn:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT doc_name, chunks, updated_at FROM rag_documents ORDER BY updated_at DESC"
                    )
                    rows = cursor.fetchall()
        except pymysql.MySQLError as exc:
            raise DocStoreUnavailable(f"MySQL 读取文档切块失败: {exc}") from exc
        docs: list[dict] = []
        for row in rows:
            try:
                chunks = json.loads(row["chunks"])
            except (TypeError, json.JSONDecodeError):
                logger.warning("文档 %s 的 chunks JSON 损坏，跳过", row.get("doc_name"))
                continue
            docs.append({
                "doc_name": row["doc_name"],
                "chunks": chunks,
                "updated_at": row["updated_at"],
            })
        return docs


_build_doc_store = None


def build_doc_store() -> DocStore | None:
    """按环境变量创建文档云存储；不可用时返回 None（调用方降级跳过）。"""
    if not os.environ.get("MYSQL_HOST"):
        return None
    try:
        return DocStore()
    except DocStoreUnavailable:
        return None
