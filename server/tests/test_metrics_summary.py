"""性能摘要聚合 + /metrics/summary 接口测试。

覆盖：
- MemoryEvaluationRecorder.summary() 的各率 / 分位数 / 维度分布语义；
- 工具成功率（ok 记成功、error/timeout 记失败、none 不计）；
- /metrics/summary 接口鉴权（未登录 401 / 客户角色 403 / 运营 200 JSON）。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.metrics_summary import router
from server.services.metrics import MemoryEvaluationRecorder, _percentile


def _recorder() -> MemoryEvaluationRecorder:
    rec = MemoryEvaluationRecorder()
    rec.record_event({
        "conversation_id": "c1", "intent": "order_query", "response_mode": "answer",
        "citation_valid": True, "needs_human": False, "needs_clarification": False,
        "tool_status": "ok", "latency_ms": 800,
    })
    rec.record_event({
        "conversation_id": "c2", "intent": "order_query", "response_mode": "clarify",
        "citation_valid": True, "needs_human": False, "needs_clarification": True,
        "tool_status": "ok", "latency_ms": 300,
    })
    rec.record_event({
        "conversation_id": "c3", "intent": "general", "response_mode": "handoff",
        "citation_valid": False, "needs_human": True, "needs_clarification": False,
        "tool_status": "error", "latency_ms": 2000,
    })
    rec.record_event({
        "conversation_id": "c4", "intent": "general", "response_mode": "answer",
        "citation_valid": None, "needs_human": False, "needs_clarification": False,
        "tool_status": "none", "error": "boom", "latency_ms": 5000,
    })
    return rec


def test_percentile_edge_cases():
    assert _percentile([], 0.5) is None
    assert _percentile([10.0], 0.5) == 10.0
    assert _percentile([100.0, 200.0, 300.0, 400.0], 0.5) == 250.0


def test_memory_summary_counts_and_rates():
    s = _recorder().summary()
    assert s["storage"] == "memory"
    assert s["total"] == 4
    # 一次解决：c1（无追问/无转人工/无错误）；c4 有 error 不计
    assert s["resolved"] == 1
    assert s["handoff"] == 1  # c3
    assert s["clarification"] == 1  # c2
    assert s["errors"] == 1  # c4
    # 工具：ok x2 成功，error x1 失败，none 不计
    assert s["tool_success"] == 2
    assert s["tool_failure"] == 1
    # 引用：True x2、False x1、None x1（不计入）
    assert s["citation_valid"] == 2
    assert s["citation_invalid"] == 1


def test_memory_summary_latency_percentiles():
    s = _recorder().summary()
    # 800 / 300 / 2000 / 5000 → avg 2025.0
    assert s["latency_avg_ms"] == 2025.0
    # 排序 [300, 800, 2000, 5000]，p50 线性插值 = 1400.0；p95 = 2000 + 0.85*(5000-2000) = 4550.0
    assert s["latency_p50_ms"] == 1400.0
    assert s["latency_p95_ms"] == 4550.0


def test_memory_summary_distributions():
    s = _recorder().summary()
    assert s["by_intent"] == {"order_query": 2, "general": 2}
    assert s["by_response_mode"] == {"answer": 2, "clarify": 1, "handoff": 1}
    assert s["by_tool_status"] == {"ok": 2, "error": 1, "none": 1}


def test_empty_summary():
    s = MemoryEvaluationRecorder().summary()
    assert s["total"] == 0
    assert s["latency_avg_ms"] is None
    assert s["latency_p50_ms"] is None


def _app_with_recorder(rec) -> FastAPI:
    from server.services.customer_service import customer_service

    app = FastAPI()
    app.include_router(router)
    customer_service._recorder = rec
    return app


def _auth_headers(role: str) -> dict[str, str]:
    """用真实账号系统签发 token（operator / customer）。"""
    from server.auth.accounts import list_users
    from server.auth.tokens import create_access_token

    for user in list_users():
        if user.role == role:
            token = create_access_token(user)
            return {"Authorization": f"Bearer {token}"}
    raise AssertionError(f"no {role} account")


def test_summary_endpoint_requires_operator():
    from server.services.customer_service import customer_service

    rec = _recorder()
    original = customer_service._recorder
    app = FastAPI()
    app.include_router(router)
    try:
        customer_service._recorder = rec
        client = TestClient(app)

        assert client.get("/metrics/summary").status_code == 401

        customer_headers = _auth_headers("customer")
        assert client.get("/metrics/summary", headers=customer_headers).status_code == 403

        operator_headers = _auth_headers("operator")
        resp = client.get("/metrics/summary", headers=operator_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4
        assert body["storage"] == "memory"
    finally:
        customer_service._recorder = original
