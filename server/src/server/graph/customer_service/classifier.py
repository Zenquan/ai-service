"""客服 MVP 意图识别。

先用确定性的轻量分类器建立稳定流程契约，后续可替换为 LLM 分类器，
但输出结构保持不变。
"""
from __future__ import annotations

import re
from typing import Callable

from server.graph.customer_service.state import CustomerServiceState, Intent

Classifier = Callable[[str], dict]

_RULES: tuple[tuple[Intent, tuple[str, ...], float], ...] = (
    ("greeting", ("你好", "您好", "hello", "hi", "在吗", "哈喽"), 0.99),
    ("complaint", ("投诉", "举报", "欺骗", "骗子", "人工客服", "不满意", "赔偿"), 0.98),
    ("order_query", ("订单", "物流", "快递", "配送", "发货", "收货"), 0.96),
    ("after_sale", ("退款", "退货", "换货", "售后", "维修", "质量问题"), 0.96),
)


def classify_query(query: str) -> dict:
    normalized = query.strip().lower()
    for intent, keywords, confidence in _RULES:
        if any(keyword in normalized for keyword in keywords):
            return {"intent": intent, "intent_confidence": confidence, "slots": {}}
    if len(re.sub(r"\s+", "", normalized)) >= 4:
        return {"intent": "knowledge_question", "intent_confidence": 0.75, "slots": {}}
    return {"intent": "unknown", "intent_confidence": 0.35, "slots": {}}


def classify_intent(state: CustomerServiceState, classifier: Classifier | None = None) -> CustomerServiceState:
    query = state.get("current_query") or state.get("messages", [{}])[-1].get("content", "")
    result = (classifier or classify_query)(query)
    explicit_intent = result.get("intent")
    restored_intent = state.get("restored_intent")
    if (
        explicit_intent == "knowledge_question"
        and restored_intent == "order_query"
        and state.get("restored_needs_clarification")
    ):
        # 上轮澄清等待补充业务信息时，本轮短消息（如订单号）不能误判成知识问答。
        result = {
            "intent": restored_intent,
            "intent_confidence": 0.9,
            "slots": state.get("restored_slots", {}),
        }
    out = dict(state)
    out.update(result)
    out["current_query"] = query
    if state.get("restored_needs_clarification") and state.get("restored_slots"):
        merged = dict(state.get("restored_slots", {}))
        merged.update(state.get("slots", {}))
        out["slots"] = merged
    out["restored_intent"] = None
    out["restored_needs_clarification"] = False
    out["restored_slots"] = {}
    return out
