"""三层路由测试：快速意图 → RAG 知识优先 → 无素材澄清/转人工。"""
from __future__ import annotations

import pytest

from server.services import rag as rag_service
from server.services.customer_service import customer_service


@pytest.fixture(autouse=True)
def clean_state():
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()
    yield
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()
    customer_service._graph_checked = True
    customer_service._graph_agent = None


def _no_material_stream(_query, **_kwargs):
    yield {"event": "no_material", "reason": "no_relevant", "material_count": 0}


def _knowledge_stream(query, **_kwargs):
    yield {"event": "token", "text": f"知识回答:{query}"}
    yield {"event": "done", "answer": f"知识回答:{query}", "citations": [], "citation_valid": True, "material_count": 0}


def test_knowledge_question_first_miss_clarifies_with_reason(monkeypatch):
    monkeypatch.setattr(rag_service, "ask_stream", _no_material_stream)

    events = list(customer_service.stream_message("layer-kb-1", "AI是什么", user_id="demo-user"))

    progress = events[0]
    meta = events[1]
    assert progress["event"] == "progress" and progress["stage"] == "rag"
    assert meta["event"] == "meta"
    assert meta["response_mode"] == "clarify"
    assert meta["intent"] == "knowledge_question"
    assert "知识库中没有找到足够相关的资料" in meta["clarify_reason"]
    assert events[-1]["event"] == "done"


def test_second_knowledge_miss_handoffs(monkeypatch):
    monkeypatch.setattr(rag_service, "ask_stream", _no_material_stream)

    list(customer_service.stream_message("layer-kb-2", "AI是什么", user_id="demo-user"))
    events = list(customer_service.stream_message("layer-kb-2", "人工智能", user_id="demo-user"))

    meta = events[1]
    assert meta["event"] == "meta"
    assert meta["response_mode"] == "handoff"
    assert meta["needs_human"] is True
    assert "连续两轮" in meta["answer"]


def test_knowledge_miss_does_not_hijack_next_order_slot(monkeypatch):
    customer_service._graph_checked = False
    customer_service._graph_agent = None
    monkeypatch.setattr(rag_service, "ask_stream", _knowledge_stream)

    first = list(customer_service.stream_message("layer-kb-3", "帮我查订单", user_id="demo-user"))
    assert first[0]["event"] == "meta" and first[0]["response_mode"] == "clarify"

    second = list(customer_service.stream_message("layer-kb-3", "AI是什么", user_id="demo-user"))
    assert second[0]["event"] == "progress"
    assert second[-1]["event"] == "done"
    assert "知识回答:AI是什么" in second[-1]["answer"]
