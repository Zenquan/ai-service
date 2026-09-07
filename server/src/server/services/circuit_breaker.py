"""工具调用熔断器：closed/open/half-open 状态机，MySQL 持久化 + 内存权威。

设计要点（迭代「限流、熔断与审计日志」P0）：
- 目标是让下游工具（当前仅 ``query_order_status``）在连续故障时快速失败，避免
  每次请求都打到不稳定的订单服务、拖慢整轮对话。
- 状态机：closed（正常放行）→ 连续失败 ``failure_threshold`` 次 → open（拒绝），
  冷却 ``cooldown_seconds`` 后 → half_open（放行一个探测请求），探测成功 → closed，
  失败 → 重新 open。
- 服务当前为单进程（``uvicorn --workers 1``，Qdrant local 单进程锁），因此内存状态
  是权威且线程安全的；``MysqlCircuitBreaker`` 只在每次状态变化后把快照写回 MySQL，
  用于重启恢复。MySQL 写入失败静默——不阻断熔断判定本身。
- 只把 ``timeout`` / ``error`` 计入失败；``not_found`` / ``forbidden`` 属业务语义，不触发熔断。

表结构：
- circuit_breaker_state(
    name VARCHAR(64) PRIMARY KEY,
    state VARCHAR(16) NOT NULL,          -- closed / open / half_open
    failure_count INT NOT NULL DEFAULT 0,
    last_failure_at DATETIME NULL,
    opened_at DATETIME NULL,
    updated_at DATETIME NOT NULL
  )
"""
from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 熔断参数（可用环境变量覆盖）。
CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
CIRCUIT_COOLDOWN_SECONDS = float(os.getenv("CIRCUIT_COOLDOWN_SECONDS", "30"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class CircuitBreaker(ABC):
    """熔断器抽象。"""

    @abstractmethod
    def allow(self, name: str) -> bool:
        """是否允许本次调用（True=放行；open 冷却中/half_open 探测中返回 False）。"""

    @abstractmethod
    def record_success(self, name: str) -> None:
        """调用成功后回执。"""

    @abstractmethod
    def record_failure(self, name: str) -> None:
        """调用失败（超时/异常）后回执。"""

    @abstractmethod
    def state(self, name: str) -> str:
        """当前状态（closed / open / half_open），未知 name 返回 closed。"""


class MemoryCircuitBreaker(CircuitBreaker):
    """进程内状态机（线程安全）；单进程场景下为权威实现。"""

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        cooldown_seconds: float = CIRCUIT_COOLDOWN_SECONDS,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._states: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _get(self, name: str) -> dict:
        state = self._states.get(name)
        if state is None:
            state = {
                "state": "closed",
                "failure_count": 0,
                "last_failure_at": None,
                "opened_at": None,
            }
            self._states[name] = state
        return state

    def allow(self, name: str) -> bool:
        with self._lock:
            state = self._get(name)
            if state["state"] == "open":
                opened_at = state.get("opened_at")
                if opened_at is not None and (time.monotonic() - opened_at) >= self.cooldown_seconds:
                    # 冷却结束 → 转 half_open，放行一个探测请求。
                    state["state"] = "half_open"
                    return True
                return False
            if state["state"] == "half_open":
                # 探测进行中：只放一个，其余拒绝。
                return False
            # closed
            return True

    def record_success(self, name: str) -> None:
        with self._lock:
            state = self._get(name)
            if state["state"] == "half_open":
                state["state"] = "closed"
                state["failure_count"] = 0
            else:
                state["failure_count"] = 0
            state["last_failure_at"] = None

    def record_failure(self, name: str) -> None:
        with self._lock:
            state = self._get(name)
            state["failure_count"] = state.get("failure_count", 0) + 1
            state["last_failure_at"] = time.monotonic()
            if state["state"] == "closed" and state["failure_count"] >= self.failure_threshold:
                state["state"] = "open"
                state["opened_at"] = time.monotonic()
            elif state["state"] == "half_open":
                # 探测失败 → 立即重新 open。
                state["state"] = "open"
                state["opened_at"] = time.monotonic()

    def state(self, name: str) -> str:
        with self._lock:
            return self._get(name)["state"]


class MysqlCircuitBreaker(CircuitBreaker):
    """MySQL 持久化 + 内存权威。

    状态判定走内部 ``MemoryCircuitBreaker``（单进程权威、线程安全），每次变更后
    把快照 upsert 回 MySQL（用于重启恢复）。初始化时从 MySQL 载入历史状态。
    """

    _DDL = (
        "CREATE TABLE IF NOT EXISTS circuit_breaker_state ("
        "name VARCHAR(64) PRIMARY KEY, "
        "state VARCHAR(16) NOT NULL, "
        "failure_count INT NOT NULL DEFAULT 0, "
        "last_failure_at DATETIME NULL, "
        "opened_at DATETIME NULL, "
        "updated_at DATETIME NOT NULL"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    _UPSERT = (
        "INSERT INTO circuit_breaker_state "
        "(name, state, failure_count, last_failure_at, opened_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "state = VALUES(state), failure_count = VALUES(failure_count), "
        "last_failure_at = VALUES(last_failure_at), opened_at = VALUES(opened_at), "
        "updated_at = VALUES(updated_at)"
    )
    _SELECT_ALL = (
        "SELECT name, state, failure_count, last_failure_at, opened_at "
        "FROM circuit_breaker_state"
    )

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        cooldown_seconds: float = CIRCUIT_COOLDOWN_SECONDS,
    ) -> None:
        self._mem = MemoryCircuitBreaker(failure_threshold, cooldown_seconds)
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
        self._load()

    def _load(self) -> None:
        """从 MySQL 载入历史状态到内存（opened_at 单调时钟无法跨进程还原，
        打开瞬间视为刚发生，冷却从本次进程启动重新计时）。"""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(self._SELECT_ALL)
                    rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001 —— 载入失败走全新状态
            logger.warning("CircuitBreaker 载入历史状态失败: %s", exc)
            return
        for name, state, failure_count, _last_failure_at, _opened_at in rows:
            self._mem._states[name] = {
                "state": state if state in {"closed", "open", "half_open"} else "closed",
                "failure_count": int(failure_count or 0),
                "last_failure_at": None,
                "opened_at": time.monotonic() if state == "open" else None,
            }

    def _persist(self, name: str) -> None:
        """把当前内存状态快照 upsert 回 MySQL；失败静默（不阻断熔断判定）。"""
        state = self._mem._states.get(name)
        if state is None:
            return
        values = (
            name[:64],
            state["state"],
            int(state.get("failure_count") or 0),
            None,
            _now() if state["state"] in {"open", "half_open"} and state.get("opened_at") else None,
            _now(),
        )
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(self._UPSERT, values)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("CircuitBreaker 持久化失败（忽略）: %s", exc)

    def allow(self, name: str) -> bool:
        allowed = self._mem.allow(name)
        if not allowed:
            return False
        # 放行（含 open→half_open 的探测放行）都写回快照。
        self._persist(name)
        return True

    def record_success(self, name: str) -> None:
        self._mem.record_success(name)
        self._persist(name)

    def record_failure(self, name: str) -> None:
        self._mem.record_failure(name)
        self._persist(name)

    def state(self, name: str) -> str:
        return self._mem.state(name)


def build_default_circuit_breaker() -> CircuitBreaker:
    """按环境变量构造熔断器；无 MySQL 或建表失败回退内存。"""
    host = os.getenv("MYSQL_HOST", "").strip()
    if not host:
        return MemoryCircuitBreaker()
    try:
        breaker: CircuitBreaker = MysqlCircuitBreaker(
            host=host,
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE") or os.getenv("MYSQL_DB") or "rag",
        )
        breaker.ensure_schema()  # type: ignore[attr-defined]
        logger.info("CircuitBreaker: MySQL (%s)", host)
        return breaker
    except Exception as exc:  # noqa: BLE001
        logger.warning("CircuitBreaker: MySQL 不可用，回退内存（%s）", exc)
        return MemoryCircuitBreaker()


__all__ = [
    "CircuitBreaker",
    "MemoryCircuitBreaker",
    "MysqlCircuitBreaker",
    "build_default_circuit_breaker",
    "CIRCUIT_FAILURE_THRESHOLD",
    "CIRCUIT_COOLDOWN_SECONDS",
]
