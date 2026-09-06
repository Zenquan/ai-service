"""LLM 意图分类器与低置信度自适应追问测试。

不真正调用 DeepSeek：通过注入 fake OpenAI client 或构造带 clarify_reason 的
分类器，验证分类契约、低置信度追问、失败回退三条链路。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from server.graph.customer_service import create_customer_service_agent
from server.graph.customer_service.classifier import classify_intent, classify_query
from server.graph.customer_service.llm_classifier import (
    LLMIntentClassifier,
    _extract_json,
    _sanitize,
)


# ---------------------------------------------------------------------------
# JSON 解析 / 归一化
# ---------------------------------------------------------------------------

def test_extract_json_tolerates_markdown_fence():
    raw = '```json\n{"intent": "order_query", "confidence": 0.9, "slots": {"order_no": "A00001"}}\n```'
    assert _extract_json(raw)["intent"] == "order_query"


def test_extract_json_tolerates_leading_text():
    raw = '好的，分类结果是 {"intent": "greeting", "confidence": 0.99, "slots": {}}'
    assert _extract_json(raw)["intent"] == "greeting"


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("不是 JSON") is None


def test_sanitize_rejects_unknown_intent():
    assert _sanitize({"intent": "hack_the_planet", "confidence": 0.9, "slots": {}}) is None


def test_sanitize_clamps_confidence():
    out = _sanitize({"intent": "unknown", "confidence": 5.0, "slots": {}})
    assert out["intent_confidence"] == 1.0


# ---------------------------------------------------------------------------
# LLM 分类器：契约、低置信度追问、失败回退
# ---------------------------------------------------------------------------

def _fake_completion(content: str):
    """构造一个返回指定 content 的 fake OpenAI client。"""
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    return client


def test_llm_classifier_returns_contract_shape():
    fake = _fake_completion(
        '{"intent": "order_query", "confidence": 0.95, "slots": {"order_no": "A00001"}}'
    )
    cls = LLMIntentClassifier(model="m", base_url="b", api_key="k")
    with patch("server.graph.customer_service.llm_classifier.OpenAI", return_value=fake):
        result = cls("查一下 A00001 的物流")
    assert result["intent"] == "order_query"
    assert result["intent_confidence"] == 0.95
    assert result["slots"] == {"order_no": "A00001"}
    assert "clarify_reason" not in result  # 高置信度不应追问


def test_llm_classifier_low_confidence_triggers_clarify():
    fake = _fake_completion(
        '{"intent": "knowledge_question", "confidence": 0.4, "slots": {}}'
    )
    # 追问生成也走同一 client，第二次调用返回追问文案
    fake.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content='{"intent": "knowledge_question", "confidence": 0.4, "slots": {}}'))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content="请问您想了解哪方面的产品信息呢？"))]),
    ]
    cls = LLMIntentClassifier(model="m", base_url="b", api_key="k")
    with patch("server.graph.customer_service.llm_classifier.OpenAI", return_value=fake):
        result = cls("那个")
    assert result["clarify_reason"] == "请问您想了解哪方面的产品信息呢？"


def test_llm_classifier_falls_back_to_rules_without_key():
    cls = LLMIntentClassifier(model="m", base_url="b", api_key="")
    # 无 key 直接回退规则分类器，不抛异常
    result = cls("帮我查订单物流")
    assert result["intent"] == "order_query"


def test_llm_classifier_falls_back_on_error():
    fake = _fake_completion('{"intent": "order_query", "confidence": 0.9, "slots": {}}')
    fake.chat.completions.create.side_effect = RuntimeError("boom")
    cls = LLMIntentClassifier(model="m", base_url="b", api_key="k")
    with patch("server.graph.customer_service.llm_classifier.OpenAI", return_value=fake):
        result = cls("帮我查订单物流")
    # 失败回退规则分类器，仍返回合法契约
    assert result["intent"] == "order_query"


# ---------------------------------------------------------------------------
# classify_intent 消费 clarify_reason → 自适应追问进 clarify 分支
# ---------------------------------------------------------------------------

def _retriever(contexts):
    def search(query: str, top_k: int = 5) -> list[dict]:
        return contexts[:top_k]
    return search


def _generator(answer: str):
    def generate(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
        return answer, []
    return generate


def test_low_confidence_adaptive_clarify_routes_to_clarify_not_retrieve():
    """低置信度追问应进入 clarify，而不是被硬路由到 RAG 检索。"""
    def classifier(query: str) -> dict:
        return {
            "intent": "knowledge_question",
            "intent_confidence": 0.4,
            "slots": {},
            "clarify_reason": "您是想查订单，还是咨询产品使用问题呢？",
        }

    agent = create_customer_service_agent(
        retriever=_retriever([]),
        generator=_generator("不应调用模型"),
        classifier=classifier,
    )

    result = agent.ask("帮我弄一下那个", conversation_id="c-adaptive")

    assert result["response_mode"] == "clarify"
    assert result["needs_clarification"] is True
    assert result["final_answer"] == "您是想查订单，还是咨询产品使用问题呢？"
    assert result["contexts"] == []  # 未进入 retrieve


def test_classify_intent_consumes_clarify_reason_into_state():
    state = {
        "current_query": "那个啥",
        "messages": [],
        "intent": None,
        "slots": {},
        "needs_clarification": False,
    }
    def classifier(query: str) -> dict:
        return {"intent": "unknown", "intent_confidence": 0.3, "slots": {}, "clarify_reason": "请问您具体需要什么帮助？"}

    out = classify_intent(state, classifier)
    assert out["clarify_reason"] == "请问您具体需要什么帮助？"
    assert out["clarify_answer"] == "请问您具体需要什么帮助？"
    assert out["intent"] == "unknown"


def test_classify_intent_without_clarify_reason_keeps_default():
    state = {
        "current_query": "帮我查订单物流",
        "messages": [],
        "intent": None,
        "slots": {},
        "needs_clarification": False,
    }
    out = classify_intent(state, classify_query)
    assert out["intent"] == "order_query"
    assert "clarify_answer" not in out
