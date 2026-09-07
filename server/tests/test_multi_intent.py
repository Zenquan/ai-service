"""多意图识别测试：规则分类器风险优先级仲裁 + 图级转人工。

迭代「多意图识别」：规则分类器不再「首个关键词命中即返回」，而是收集全部命中意图，
按风险优先级（投诉 > 售后 > 订单 > 寒暄）仲裁主意图，并把全部意图暴露到 state。
"""
from __future__ import annotations

from server.graph.customer_service import create_customer_service_agent
from server.graph.customer_service.classifier import (
    classify_intent,
    classify_query,
    detect_intents,
)
from server.graph.customer_service.llm_classifier import _sanitize


# ---------------------------------------------------------------------------
# detect_intents / classify_query：多意图检测与仲裁
# ---------------------------------------------------------------------------

def test_detect_intents_returns_all_matches():
    assert detect_intents("我既要查订单物流，还要退款") == ["order_query", "after_sale"]
    assert detect_intents("你好") == ["greeting"]
    assert detect_intents("我要退款还要投诉你们") == ["complaint", "after_sale"]


def test_classify_query_arbitrates_after_sale_over_order():
    result = classify_query("我既要查订单 A00001 物流，还要退款")
    assert result["intent"] == "after_sale"
    assert result["is_multi_intent"] is True
    assert set(result["intents"]) == {"order_query", "after_sale"}


def test_classify_query_complaint_overrides_after_sale():
    # 投诉（高风险）优先于售后
    result = classify_query("我要退款，还要投诉你们")
    assert result["intent"] == "complaint"


def test_classify_query_single_intent_not_multi():
    result = classify_query("帮我查订单物流")
    assert result["intent"] == "order_query"
    assert result["is_multi_intent"] is False
    assert result["intents"] == ["order_query"]


def test_classify_query_fallback_knowledge_and_unknown():
    assert classify_query("你们支持哪些支付方式")["intent"] == "knowledge_question"
    assert classify_query("嗯")["intent"] == "unknown"


def test_classify_intent_writes_multi_intent_into_state():
    state = {
        "current_query": "查订单 A00001 还要退款",
        "messages": [],
        "intent": None,
        "slots": {},
        "needs_clarification": False,
    }
    out = classify_intent(state, classify_query)
    assert out["intent"] == "after_sale"
    assert out["is_multi_intent"] is True
    assert "order_query" in out["intents"]
    assert "after_sale" in out["intents"]


# ---------------------------------------------------------------------------
# LLM 分类器契约兼容
# ---------------------------------------------------------------------------

def test_sanitize_preserves_valid_intents():
    out = _sanitize({
        "intent": "after_sale",
        "confidence": 0.9,
        "slots": {},
        "intents": ["order_query", "after_sale", "hack_the_planet"],
    })
    assert out["intent"] == "after_sale"
    assert out["is_multi_intent"] is True
    assert out["intents"] == ["order_query", "after_sale"]  # 非法意图被剔除


def test_sanitize_without_intents_keeps_single_intent_shape():
    out = _sanitize({"intent": "order_query", "confidence": 0.9, "slots": {}})
    assert out["intent"] == "order_query"
    assert "is_multi_intent" not in out


# ---------------------------------------------------------------------------
# 图级：多意图路由到转人工
# ---------------------------------------------------------------------------

def _retriever(contexts):
    def search(query: str, top_k: int = 5) -> list[dict]:
        return contexts[:top_k]

    return search


def _generator(answer: str):
    def generate(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
        return answer, []

    return generate


def test_multi_intent_routes_to_handoff():
    agent = create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
    )
    result = agent.ask(
        "我既要查订单 A00001 物流，还要退款",
        conversation_id="c-multi",
        user_id="demo-user",
    )
    assert result["intent"] == "after_sale"
    assert result["is_multi_intent"] is True
    assert result["response_mode"] == "handoff"
    assert result["needs_human"] is True
    assert result["tool_calls"] == []  # 售后转人工，不调订单工具
