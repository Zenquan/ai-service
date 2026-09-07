"""熔断器测试：closed/open/half-open 状态机 + MySQL 委托 + 工厂回退。"""
from __future__ import annotations

import pytest

from server.services.circuit_breaker import (
    MemoryCircuitBreaker,
    MysqlCircuitBreaker,
    build_default_circuit_breaker,
)


def test_closed_opens_after_threshold():
    cb = MemoryCircuitBreaker(failure_threshold=3, cooldown_seconds=30)
    assert cb.allow("t") is True
    cb.record_failure("t")
    cb.record_failure("t")
    assert cb.state("t") == "closed"
    cb.record_failure("t")  # 第 3 次 → open
    assert cb.state("t") == "open"


def test_open_rejects_until_cooldown(monkeypatch):
    cb = MemoryCircuitBreaker(failure_threshold=2, cooldown_seconds=10)
    now = [0.0]
    monkeypatch.setattr("server.services.circuit_breaker.time.monotonic", lambda: now[0])
    cb.record_failure("t")
    cb.record_failure("t")
    assert cb.state("t") == "open"
    assert cb.allow("t") is False  # 冷却中拒绝
    now[0] += 10.0  # 冷却结束 → half_open 放一个探测
    assert cb.allow("t") is True
    assert cb.allow("t") is False  # half_open 只放一个
    cb.record_success("t")
    assert cb.state("t") == "closed"


def test_half_open_failure_reopens():
    cb = MemoryCircuitBreaker(failure_threshold=2, cooldown_seconds=0)
    cb.record_failure("t")
    cb.record_failure("t")
    assert cb.state("t") == "open"
    assert cb.allow("t") is True  # cooldown=0 → 立即 half_open
    cb.record_failure("t")  # 探测失败 → 重新 open
    assert cb.state("t") == "open"


def test_success_resets_counter():
    cb = MemoryCircuitBreaker(failure_threshold=3, cooldown_seconds=30)
    cb.record_failure("t")
    cb.record_failure("t")
    cb.record_success("t")  # closed 下成功 → 清零
    cb.record_failure("t")
    cb.record_failure("t")
    assert cb.state("t") == "closed"


def test_unknown_name_closed():
    cb = MemoryCircuitBreaker()
    assert cb.state("nope") == "closed"


def test_mysql_circuit_breaker_delegates_to_memory(monkeypatch):
    cb = MysqlCircuitBreaker(
        host="h", port=3306, user="u", password="p", database="d",
        failure_threshold=2, cooldown_seconds=30,
    )
    monkeypatch.setattr(cb, "_persist", lambda name: None)  # 不连库
    assert cb.allow("t") is True
    cb.record_failure("t")
    cb.record_failure("t")
    assert cb.state("t") == "open"


def test_build_falls_back_to_memory_without_mysql(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    assert isinstance(build_default_circuit_breaker(), MemoryCircuitBreaker)


def test_build_falls_back_on_bad_mysql(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "1")
    assert isinstance(build_default_circuit_breaker(), MemoryCircuitBreaker)
