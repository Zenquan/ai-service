"""Prometheus 暴露层测试：指标聚合 + /metrics 路由。

覆盖：
- PrometheusEvaluator 从 MemoryEvaluationRecorder 增量聚合各业务指标；
- 一次解决 / 转人工 / 追问 / 工具成功 / 引用有效 的计数语义；
- 增量 refresh 幂等（重复刷新不重复计数）；
- /metrics 路由返回 text/plain 的 Prometheus 文本，且不泄露 *_created 序列。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from server.services.metrics import MemoryEvaluationRecorder
from server.services.prom_exporter import PrometheusEvaluator


def _sample_recorder() -> MemoryEvaluationRecorder:
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
    return rec


def test_aggregates_request_total_by_intent_and_mode():
    ev = PrometheusEvaluator(_sample_recorder())
    text = ev.render_text()
    assert 'cs_eval_requests_total{intent="order_query",response_mode="answer"} 1.0' in text
    assert 'cs_eval_requests_total{intent="order_query",response_mode="clarify"} 1.0' in text
    assert 'cs_eval_requests_total{intent="general",response_mode="handoff"} 1.0' in text


def test_resolved_handoff_clarification_counts():
    ev = PrometheusEvaluator(_sample_recorder())
    text = ev.render_text()
    # 一次解决：仅 c1（answer、无追问、无转人工、无错误）
    assert 'cs_eval_resolved_total{intent="order_query"} 1.0' in text
    # 转人工：c3
    assert 'cs_eval_human_handoff_total{intent="general"} 1.0' in text
    # 追问：c2
    assert 'cs_eval_clarification_total{intent="order_query"} 1.0' in text


def test_tool_status_and_citation_counts():
    ev = PrometheusEvaluator(_sample_recorder())
    text = ev.render_text()
    assert 'cs_eval_tool_calls_total{tool_status="ok"} 2.0' in text
    assert 'cs_eval_tool_calls_total{tool_status="error"} 1.0' in text
    assert 'cs_eval_citation_valid_total{valid="1"} 2.0' in text
    assert 'cs_eval_citation_valid_total{valid="0"} 1.0' in text


def test_latency_histogram():
    ev = PrometheusEvaluator(_sample_recorder())
    text = ev.render_text()
    assert "cs_eval_latency_seconds_count 3.0" in text
    # 800 + 300 + 2000 ms = 3.1 s
    assert "cs_eval_latency_seconds_sum 3.1" in text


def test_refresh_is_idempotent():
    rec = _sample_recorder()
    ev = PrometheusEvaluator(rec)
    ev.render()  # 第一次渲染
    ev.render()  # 第二次渲染不应重复计数
    text = ev.render_text()
    assert 'cs_eval_requests_total{intent="general",response_mode="handoff"} 1.0' in text


def test_no_created_series_by_default():
    ev = PrometheusEvaluator(_sample_recorder())
    text = ev.render_text()
    assert "_created" not in text


def test_metrics_endpoint_returns_prometheus_text():
    from server.main import app
    from server.services.customer_service import customer_service

    rec = _sample_recorder()
    # 直接绑定到单例 recorder，走真实路由
    original = customer_service.recorder
    customer_service._recorder = rec
    try:
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "cs_eval_requests_total" in resp.text
    finally:
        customer_service._recorder = original
