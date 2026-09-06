"""客服 MVP 意图识别。

先用确定性的轻量分类器建立稳定流程契约，后续可替换为 LLM 分类器，
但输出结构保持不变。
"""
from __future__ import annotations

import re
from typing import Callable

from server.graph.customer_service.state import CustomerServiceState, Intent
from server.tools.orders import extract_order_numbers

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
    explicit = (classifier or classify_query)(query)
    explicit_intent = explicit.get("intent")

    # 跨轮恢复：上一轮处于「订单澄清」且本轮仍在补订单信息时，延续 order_query
    # 与已积累的槽位，避免把裸订单号误判成知识问答或普通短文本。
    carrying_clarify = bool(state.get("needs_clarification")) and state.get("intent") == "order_query"
    continues_order = explicit_intent in {"order_query", "after_sale"} or bool(
        extract_order_numbers(query)
    )
    if carrying_clarify and continues_order:
        intent: str = "order_query"
        confidence = 0.9
        slots = dict(state.get("slots") or {})
    else:
        intent = explicit_intent
        confidence = explicit.get("intent_confidence")
        # 全新意图：槽位从空开始，避免上一轮已结单的订单号残留。
        slots = {}

    out = dict(state)
    out["intent"] = intent
    out["intent_confidence"] = confidence
    out["slots"] = slots
    out["current_query"] = query
    # 消费上一轮的澄清信号：本轮是否澄清由 clarify 节点重新判定，避免残留。
    out["needs_clarification"] = False
    # 低置信度自适应追问：classifier（LLM）可返回针对用户原话的追问文案，
    # 由 clarify 节点优先采用；未提供时回落 clarify 节点内置的通用追问。
    clarify_reason = explicit.get("clarify_reason")
    if clarify_reason:
        out["clarify_reason"] = clarify_reason
        out["clarify_answer"] = clarify_reason
    return out
