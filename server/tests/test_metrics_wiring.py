"""客服图指标埋点接线测试：customer_service._answer_with_graph 完成后产生 evaluation_event。"""
from __future__ import annotations

import pytest

from server.services import customer_service
from server.services.metrics import MemoryEvaluationRecorder
from server.graph.customer_service import create_customer_service_agent


@pytest.fixture
def fresh_recorder(monkeypatch):
    recorder = MemoryEvaluationRecorder()
    monkeypatch.setattr(customer_service.customer_service, "_recorder", recorder)
    yield recorder
    recorder.clear()


def test_answer_with_graph_records_event_with_key_fields(fresh_recorder, monkeypatch):
    """成功路径：record_event 应被调用，字段齐全。"""
    from server.graph.customer_service.classifier import Classifier
    from server.graph.customer_service.nodes import Generator, Retriever

    def fake_retriever(query, top_k=5):
        return [{"text": "AI Agent 介绍", "doc": "guide.md", "seq": 0}]

    def fake_generator(question, contexts, history):
        return "AI Agent 能规划任务[1]", [1]

    agent = create_customer_service_agent(
        retriever=fake_retriever,
        generator=fake_generator,
    )
    monkeypatch.setattr(customer_service.customer_service, "_graph_agent", agent)
    monkeypatch.setattr(customer_service.customer_service, "_graph_checked", True)

    result = customer_service.customer_service._answer_with_graph(
        conversation_id="m-1",
        message="什么是 AI Agent？",
        user_id="u-1",
        message_id="msg-1",
    )

    assert result is not None
    assert result["intent"] == "knowledge_question"
    events = fresh_recorder.events
    assert len(events) == 1
    ev = events[0]
    assert ev["conversation_id"] == "m-1"
    assert ev["message_id"] == "msg-1"
    assert ev["intent"] == "knowledge_question"
    assert ev["response_mode"] == "answer"
    assert ev["citation_valid"] is True
    assert ev["materials_count"] == 1
    assert ev["citations_count"] == 1
    assert ev["tool_calls_count"] == 0
    assert ev["tool_status"] is None
    assert isinstance(ev["latency_ms"], int)
    assert ev["latency_ms"] >= 0


def test_answer_with_graph_records_handoff_event(fresh_recorder, monkeypatch):
    """转人工路径：response_mode=handoff、needs_human=True、handoff_reason 落事件。"""
    from server.services.customer_service import customer_service as svc

    agent = create_customer_service_agent(
        retriever=lambda q, top_k=5: [],
        generator=lambda q, c, h: ("不应调用", []),
    )
    monkeypatch.setattr(svc, "_graph_agent", agent)
    monkeypatch.setattr(svc, "_graph_checked", True)

    result = svc._answer_with_graph(
        conversation_id="m-2",
        message="我要投诉",
        message_id="msg-2",
    )
    assert result is not None
    assert result["response_mode"] == "handoff"
    assert result["needs_human"] is True
    events = fresh_recorder.events
    assert len(events) == 1
    assert events[0]["response_mode"] == "handoff"
    assert events[0]["needs_human"] is True
    assert events[0]["handoff_reason"]  # 必填字段被记录


def test_answer_with_graph_records_order_tool_event(fresh_recorder, monkeypatch):
    """订单工具路径：tool_calls_count=1、tool_status=ok。"""
    agent = create_customer_service_agent(
        retriever=lambda q, top_k=5: [],
        generator=lambda q, c, h: ("不应调用", []),
    )
    monkeypatch.setattr(customer_service.customer_service, "_graph_agent", agent)
    monkeypatch.setattr(customer_service.customer_service, "_graph_checked", True)

    result = customer_service.customer_service._answer_with_graph(
        conversation_id="m-3",
        message="查一下 A00001 的物流",
        user_id="demo-user",
        message_id="msg-3",
    )
    assert result is not None
    assert result["response_mode"] == "answer"
    events = fresh_recorder.events
    assert len(events) == 1
    assert events[0]["tool_calls_count"] == 1
    assert events[0]["tool_status"] == "ok"


def test_first_tool_status_helper():
    """纯函数级单测。"""
    from server.services.customer_service import _first_tool_status

    assert _first_tool_status(None) is None
    assert _first_tool_status([]) is None
    assert _first_tool_status([{"status": "ok"}]) == "ok"
    assert _first_tool_status([{"status": "not_found"}]) == "not_found"
    # 兼容老字段 ok: bool
    assert _first_tool_status([{"ok": True}]) == "ok"
    assert _first_tool_status([{"ok": False}]) == "error"