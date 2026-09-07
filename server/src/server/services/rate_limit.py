"""请求限流：MySQL 固定窗口计数 + 内存滑动窗口回退。

设计要点（迭代「限流、熔断与审计日志」P0，明确不用 Redis）：
- 复用项目 MySQL 短连接范式（``MYSQL_HOST`` 可用时走 MySQL，否则回退内存），
  与 ``chat_store`` / ``metrics`` / ``alerts`` 同款「抽象 + 内存回退 + MySQL 实现」三段式。
- ``RateLimiter.check(key, limit, window_seconds) -> bool``：True=允许，False=拒绝。
- ``MemoryRateLimiter``：精确滑动窗口（每 key 一个时间戳 deque），进程内线程安全，重启即清零。
- ``MysqlRateLimiter``：固定窗口原子自增——``scope + window_no`` 复合主键，
  ``INSERT ... ON DUPLICATE KEY UPDATE count = count + 1`` 原子计数，跨进程/跨重启共享。
  固定窗口在窗口边界处可能瞬时放行 2×limit，属已知权衡（客服场景 QPS 低，可接受）。
- 旧窗口行按 ``window_no`` 概率清理，避免表无限膨胀。

表结构：
- rate_limit_counters(
    scope VARCHAR(64) NOT NULL,     -- 限流维度（如 ip:1.2.3.4 / user:alice）
    window_no BIGINT NOT NULL,      -- 窗口序号 = epoch 秒 // window_seconds
    count INT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (scope, window_no)
  )
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class RateLimiter(ABC):
    """限流器抽象。"""

    @abstractmethod
    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """判断一次请求是否被允许。

        - ``key``：限流维度（如客户端 IP、用户 ID）。
        - ``limit``：窗口内最大允许次数。
        - ``window_seconds``：窗口长度（秒）。

        返回 True 表示允许（并计入本次），False 表示超限拒绝。
        """
        ...


class MemoryRateLimiter(RateLimiter):
    """进程内精确滑动窗口；线程安全；重启即清零（无 MySQL 时的回退）。"""

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            return False
        now = time.monotonic()
        with self._lock:
            queue = self._windows.setdefault(key, deque())
            cutoff = now - window_seconds
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if len(queue) >= limit:
                return False
            queue.append(now)
            return True


class MysqlRateLimiter(RateLimiter):
    """MySQL 固定窗口原子计数实现（pymysql 短连接）。"""

    _DDL = (
        "CREATE TABLE IF NOT EXISTS rate_limit_counters ("
        "scope VARCHAR(64) NOT NULL, "
        "window_no BIGINT NOT NULL, "
        "count INT NOT NULL DEFAULT 0, "
        "updated_at DATETIME NOT NULL, "
        "PRIMARY KEY (scope, window_no)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    _UPSERT = (
        "INSERT INTO rate_limit_counters (scope, window_no, count, updated_at) "
        "VALUES (%s, %s, 1, %s) "
        "ON DUPLICATE KEY UPDATE count = count + 1, updated_at = VALUES(updated_at)"
    )
    _SELECT = "SELECT count FROM rate_limit_counters WHERE scope = %s AND window_no = %s"
    _PURGE = "DELETE FROM rate_limit_counters WHERE window_no < %s"

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

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            return False
        window_no = int(time.time()) // max(1, window_seconds)
        scope = key[:64]
        now = _now()

        # 原子自增计数
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._UPSERT, (scope, window_no, now))
                # 概率性清理旧窗口，避免表无限膨胀（每 64 个窗口清理一次）。
                if window_no % 64 == 0:
                    try:
                        cur.execute(self._PURGE, (window_no - 2,))
                    except Exception:  # noqa: BLE001 —— 清理失败不影响限流判定
                        pass
            conn.commit()

        # 读回计数判断
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(self._SELECT, (scope, window_no))
                row = cur.fetchone()
        count = int(row[0]) if row else 1
        return count <= limit


def _mysql_env() -> dict[str, str | int] | None:
    host = os.getenv("MYSQL_HOST", "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        # 兼容 chat_store/checkpoint 的 MYSQL_DB 与 metrics/alerts 的 MYSQL_DATABASE。
        "database": os.getenv("MYSQL_DATABASE") or os.getenv("MYSQL_DB") or "rag",
    }


def build_default_rate_limiter() -> RateLimiter:
    """按环境变量构造限流器；无 MySQL 或建表失败回退内存。"""
    cfg = _mysql_env()
    if cfg is None:
        return MemoryRateLimiter()
    try:
        limiter: RateLimiter = MysqlRateLimiter(**cfg)
        limiter.ensure_schema()  # type: ignore[attr-defined]
        logger.info("RateLimiter: MySQL (%s)", cfg["host"])
        return limiter
    except Exception as exc:  # noqa: BLE001 —— 限流回退内存不应阻塞业务
        logger.warning("RateLimiter: MySQL 不可用，回退内存（%s）", exc)
        return MemoryRateLimiter()


_default_limiter: RateLimiter | None = None
_default_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """进程内单例限流器（中间件使用）。"""
    global _default_limiter
    if _default_limiter is None:
        with _default_limiter_lock:
            if _default_limiter is None:
                _default_limiter = build_default_rate_limiter()
    return _default_limiter


__all__ = [
    "RateLimiter",
    "MemoryRateLimiter",
    "MysqlRateLimiter",
    "build_default_rate_limiter",
    "get_rate_limiter",
]
