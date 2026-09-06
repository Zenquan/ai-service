"""智能客服会话 API 测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.services import rag as rag_service
from server.services.customer_service import customer_service

@pytest.fixture(autouse=True)
def clear_conversations():
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()
    # API 层测试直接打桩 rag.ask，不走真实 LangGraph 图（图会连 Qdrant/模型，拖慢且不可控）
    customer_service._graph_checked = True
    customer_service._graph_agent = None
    yield
    for store in (customer_service._store, customer_service._fallback_store):
        store.clear()

@pytest.fixture()
def client():
    return TestClient(app)

def test_message_creates_session_and_persists_history(client, monkeypatch):
    monkeypatch.setattr(
        rag_service,
        "ask",
        lambda query: {
            "answer": "AI Agent 是大模型驱动的自主智能体[来源1]",
            "materials": [{"doc": "store.md", "seq": 0, "text": "AI Agent 核心能力", "score": 0.9}],
            "citations": [1],
            "citation_valid": True,
            "error": None,
        },
    )

    response = client.post("/api/v1/conversations/c-1/messages", json={"message": "经营模式是什么？"})

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == "c-1"
    assert body["response_mode"] == "answer"
    assert body["citation_valid"] is True
    assert body["storage"] in {"memory", "memory_fallback", "mysql"}
    assert client.get("/api/v1/conversations/c-1").json()["message_count"] == 2
    assert len(client.get("/api/v1/conversations/c-1/messages").json()) == 2

def test_business_request_handoffs_without_calling_rag(client, monkeypatch):
    def unexpected_call(_query):
        raise AssertionError("业务请求不应调用知识问答")

    monkeypatch.setattr(rag_service, "ask", unexpected_call)

    response = client.post("/api/v1/conversations/c-2/messages", json={"message": "帮我查订单物流"})

    assert response.status_code == 200
    body = response.json()
    assert body["response_mode"] == "handoff"
    assert body["needs_human"] is True
    assert body["handoff_reason"] == "业务工具尚未接入"
    assert client.get("/api/v1/conversations/c-2").json()["status"] == "handoff"

def test_message_validation(client):
    response = client.post("/api/v1/conversations/c-3/messages", json={"message": ""})
    assert response.status_code == 422
