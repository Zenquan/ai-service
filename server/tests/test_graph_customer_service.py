"""客服 MVP 图契约测试：知识问答、澄清和人工接管。"""
from __future__ import annotations

from server.graph.customer_service import create_customer_service_agent

def _retriever(contexts):
    def search(query: str, top_k: int = 5) -> list[dict]:
        return contexts[:top_k]

    return search

def _generator(answer: str):
    def generate(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
        return answer, []

    return generate

def test_knowledge_question_uses_rag_and_validates_citation():
    agent = create_customer_service_agent(
        retriever=_retriever([{"text": "AI Agent 介绍", "doc": "store.md", "seq": 0}]),
        generator=_generator("AI Agent 能规划任务并调用工具[1]"),
    )

    result = agent.ask("门店经营模式是什么？", conversation_id="c-1")

    assert result["intent"] == "knowledge_question"
    assert result["needs_human"] is False
    assert result["citation_valid"] is True
    assert result["citations"] == [1]
    assert result["conversation_id"] == "c-1"

def test_order_question_without_order_no_asks_clarification():
    agent = create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
    )

    result = agent.ask("帮我查一下订单物流", conversation_id="c-2")

    assert result["intent"] == "order_query"
    assert result["needs_clarification"] is True
    assert result["response_mode"] == "clarify"
    assert result["needs_human"] is False
    assert "订单号" in result["final_answer"]


def test_order_question_returns_own_order_without_fabrication():
    agent = create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
    )

    result = agent.ask(
        "帮我查一下订单 A00001 的物流",
        conversation_id="c-2",
        user_id="demo-user",
    )

    assert result["intent"] == "order_query"
    assert result["response_mode"] == "answer"
    assert result["needs_human"] is False
    assert result["tool_results"][0]["status"] == "ok"
    assert "运输中" in result["final_answer"]
    assert "顺丰速运" in result["final_answer"]


def test_order_question_denies_other_users_order():
    agent = create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
    )

    result = agent.ask(
        "查一下 A00001 的物流到哪了",
        conversation_id="c-2b",
        user_id="alice",
    )

    assert result["intent"] == "order_query"
    assert result["needs_human"] is True
    assert result["response_mode"] == "handoff"
    assert result["handoff_reason"] == "非本人订单，权限拒绝"
    assert "不属于当前会话用户" in result["final_answer"]

def test_low_confidence_request_asks_for_clarification():
    agent = create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
    )

    result = agent.ask("帮帮我", conversation_id="c-3")

    assert result["intent"] == "unknown"
    assert result["needs_clarification"] is True
    assert "产品" in result["final_answer"] or "订单" in result["final_answer"]

def test_source_citation_format_is_compatible_with_rag():
    agent = create_customer_service_agent(
        retriever=_retriever([{"text": "依据", "doc": "guide.md", "seq": 0}]),
        generator=_generator("依据如下[来源1]"),
    )

    result = agent.ask("依据是什么？")

    assert result["citation_valid"] is True
    assert result["citations"] == [1]
