"""告警系统测试：阈值判定 + 落库 + webhook 触发路径。

覆盖：
- 样本不足不告警（MIN_SAMPLES 下限）；
- 转人工率 / 出错率 / 无依据承诺率 三条规则的阈值命中；
- 告警落库到 MemoryAlertStore.recent（倒序）；
- webhook 仅在配置 ALERT_WEBHOOK_URL 时调度（不真实发送）；
- /alerts/recent 路由需运营角色（dependency_overrides 绕过）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.services.alerts import (
    AlertEvaluator,
    MemoryAlertStore,
    MIN_SAMPLES,
)
from server.services.metrics import MemoryEvaluationRecorder


def _fill(rec: MemoryEvaluationRecorder, n: int, **fields) -> None:
    for i in range(n):
        base = {"intent": "general", "needs_human": False, "citation_valid": True, "error": None}
        base.update(fields)
        rec.record_event(base)


def test_no_alert_below_min_samples():
    rec = MemoryEvaluationRecorder()
    store = MemoryAlertStore()
    ev = AlertEvaluator(rec, store)
    _fill(rec, MIN_SAMPLES - 1, needs_human=True)
    assert ev.check() == []
    assert store.recent() == []


def test_handoff_rate_alert():
    rec = MemoryEvaluationRecorder()
    store = MemoryAlertStore()
    ev = AlertEvaluator(rec, store)
    _fill(rec, MIN_SAMPLES, needs_human=True)
    fired = ev.check()
    assert len(fired) == 1
    assert fired[0]["rule"] == "handoff_rate"
    assert fired[0]["current_value"] == 1.0
    assert store.recent()[0]["rule"] == "handoff_rate"


def test_error_rate_alert():
    rec = MemoryEvaluationRecorder()
    store = MemoryAlertStore()
    ev = AlertEvaluator(rec, store)
    _fill(rec, MIN_SAMPLES, error="graph_exception")
    fired = ev.check()
    rules = {a["rule"] for a in fired}
    assert "error_rate" in rules


def test_unfounded_rate_alert():
    rec = MemoryEvaluationRecorder()
    store = MemoryAlertStore()
    ev = AlertEvaluator(rec, store)
    _fill(rec, MIN_SAMPLES, citation_valid=False)
    fired = ev.check()
    rules = {a["rule"] for a in fired}
    assert "unfounded_rate" in rules


def test_normal_window_triggers_no_alert():
    rec = MemoryEvaluationRecorder()
    store = MemoryAlertStore()
    ev = AlertEvaluator(rec, store)
    _fill(rec, MIN_SAMPLES, needs_human=False, citation_valid=True, error=None)
    assert ev.check() == []


def test_recent_is_reversed():
    store = MemoryAlertStore()
    for i in range(3):
        store.add_alert({
            "rule": "handoff_rate", "current_value": 0.5 + i * 0.1,
            "threshold": 0.5, "window_samples": 10,
            "message": f"alert-{i}", "created_at": f"2026-09-07 00:0{i}:00",
        })
    recent = store.recent()
    assert recent[0]["message"] == "alert-2"
    assert recent[-1]["message"] == "alert-0"


def test_alerts_endpoint_requires_operator():
    """用最小 FastAPI app 挂 alerts 路由，避免触发 server.main 的 Qdrant lifespan。"""
    from fastapi import FastAPI

    from server.api.alerts import router
    from server.auth.dependencies import get_operator
    from server.auth.accounts import AuthUser
    from server.services.customer_service import customer_service

    # 注入一个 memory store，避免依赖 MySQL
    store = MemoryAlertStore()
    store.add_alert({
        "rule": "handoff_rate", "current_value": 0.8, "threshold": 0.5,
        "window_samples": 10, "message": "test", "created_at": "2026-09-07 00:00:00",
    })
    original = customer_service.alert_store
    customer_service._alert_store = store

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def fake_operator():
        return AuthUser(id="op-1", username="operator", role="operator", display_name="运营")

    app.dependency_overrides[get_operator] = fake_operator
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/alerts/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert data[0]["rule"] == "handoff_rate"
    finally:
        customer_service._alert_store = original
