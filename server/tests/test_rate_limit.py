"""限流器测试：内存滑动窗口行为 + 中间件 429 + 工厂回退。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.services.middleware import RateLimitMiddleware
from server.services.rate_limit import (
    MemoryRateLimiter,
    build_default_rate_limiter,
)


def test_memory_limits_within_window():
    limiter = MemoryRateLimiter()
    key = "ip:1.2.3.4"
    for _ in range(3):
        assert limiter.check(key, limit=3, window_seconds=60) is True
    assert limiter.check(key, limit=3, window_seconds=60) is False


def test_memory_isolates_keys():
    limiter = MemoryRateLimiter()
    assert limiter.check("a", limit=1, window_seconds=60) is True
    assert limiter.check("b", limit=1, window_seconds=60) is True
    assert limiter.check("a", limit=1, window_seconds=60) is False


def test_memory_window_slides(monkeypatch):
    limiter = MemoryRateLimiter()
    now = [1000.0]
    monkeypatch.setattr("server.services.rate_limit.time.monotonic", lambda: now[0])
    assert limiter.check("k", limit=1, window_seconds=10) is True
    assert limiter.check("k", limit=1, window_seconds=10) is False
    now[0] += 10.0  # 窗口过期后可再次放行
    assert limiter.check("k", limit=1, window_seconds=10) is True


def test_zero_limit_rejects():
    limiter = MemoryRateLimiter()
    assert limiter.check("a", limit=0, window_seconds=60) is False


def test_build_falls_back_to_memory_without_mysql(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    assert isinstance(build_default_rate_limiter(), MemoryRateLimiter)


def test_build_falls_back_on_bad_mysql(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "1")
    assert isinstance(build_default_rate_limiter(), MemoryRateLimiter)


def test_middleware_returns_429_when_limited(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    app = FastAPI()
    limiter = MemoryRateLimiter()
    app.add_middleware(RateLimitMiddleware, limiter=limiter, limit=1, window_seconds=60)

    @app.post("/api/v1/conversations/c1/messages")
    async def msg():
        return {"ok": True}

    client = TestClient(app)
    r1 = client.post("/api/v1/conversations/c1/messages", json={"message": "hi"})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/conversations/c1/messages", json={"message": "hi"})
    assert r2.status_code == 429
    assert r2.headers.get("Retry-After") == "60"


def test_middleware_skips_non_message_paths(monkeypatch):
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    app = FastAPI()
    limiter = MemoryRateLimiter()
    app.add_middleware(RateLimitMiddleware, limiter=limiter, limit=1, window_seconds=60)

    @app.get("/api/v1/conversations/c1/messages")
    async def get_messages():
        return {"ok": True}

    client = TestClient(app)
    for _ in range(3):
        assert client.get("/api/v1/conversations/c1/messages").status_code == 200
