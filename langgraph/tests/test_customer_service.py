"""客服 MVP 图契约测试：知识问答、澄清和人工接管。"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from langgraph_graph.customer_service import create_customer_service_agent


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
        retriever=_retriever([{"text": "日清模式", "doc": "store.md", "seq": 0}]),
        generator=_generator("门店采用日清模式[1]"),
    )

    result = agent.ask("门店经营模式是什么？", conversation_id="c-1")

    assert result["intent"] == "knowledge_question"
    assert result["needs_human"] is False
    assert result["citation_valid"] is True
    assert result["citations"] == [1]
    assert result["conversation_id"] == "c-1"


def test_order_question_is_not_fabricated_as_completed():
    agent = create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
    )

    result = agent.ask("帮我查一下订单物流", conversation_id="c-2")

    assert result["intent"] == "order_query"
    assert result["needs_human"] is True
    assert result["handoff_reason"] == "业务工具尚未接入"
    assert "不能直接查询" in result["final_answer"]


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
