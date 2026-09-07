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

# 多意图仲裁优先级：数值越大越优先当选「主意图」。售后/投诉风险最高（可能涉及
# 不可逆操作），优先转人工；订单查询次之；寒暄最低（常与业务词混用）。
_INTENT_PRIORITY: dict[str, int] = {
    "complaint": 40,
    "after_sale": 30,
    "order_query": 20,
    "greeting": 10,
}

# 对抗样本归一化：去掉空白与常见混淆符号（全角/半角空格、波浪线、引号、下划线、
# 星号、竖线、顿号、间隔号等），使「退 款」「退~款」「退_款」仍能命中「退款」关键词，
# 防止攻击者用拆字/加符绕过确定性规则。仅用于意图/关键词匹配，不改动原始 query。
_CONFUSER_RE = re.compile(r"[\s\u00a0\u3000~·`´‘’“”'\"*_｜|、]+")


def _normalize_query(query: str) -> str:
    return _CONFUSER_RE.sub("", query.strip().lower())


# 提示注入检测模式：(主词, 需同时出现的上下文词)。
# 命中任一模式即视为注入攻击 → 归 complaint（高风险转人工），不进入知识问答/工具链路。
# 上下文词用于区分正常表达：如「忽略我的上一条消息」是正常用户，不判注入；而
# 「忽略以上指令/安全规则」才是攻击。无上下文词（tails 为空）表示该词本身即攻击信号。
_INJECTION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("忽略", ("指令", "提示", "设定", "角色", "规则", "限制", "之前", "以上", "安全")),
    ("无视", ("指令", "提示", "规则", "限制", "安全", "设定")),
    ("系统提示", ()),
    ("系统指令", ()),
    ("提示词", ("泄露", "泄漏", "输出", "原文", "给我", "发", "打印")),
    ("泄露", ("提示词", "系统", "指令", "知识库", "内部")),
    ("知识库", ("内容", "原文", "全部", "打印", "输出", "导出", "给我", "泄露")),
    ("越狱", ()),
    ("jailbreak", ()),
    ("角色扮演", ()),
    ("解除", ("限制", "屏蔽", "过滤", "审查", "规则")),
)


def _detect_injection(normalized: str) -> bool:
    for head, tails in _INJECTION_PATTERNS:
        if head not in normalized:
            continue
        if not tails or any(t in normalized for t in tails):
            return True
    return False


def _confidence_for(intent: str) -> float:
    for it, _, confidence in _RULES:
        if it == intent:
            return confidence
    return 0.5


def detect_intents(query: str) -> list[str]:
    """返回命中的全部意图（按 ``_RULES`` 声明顺序，去重）。

    匹配前先做对抗样本归一化（去空白/混淆符号），防止「退 款」「投~诉」绕过。
    """
    normalized = _normalize_query(query)
    matched: list[str] = []
    for intent, keywords, _ in _RULES:
        if intent not in matched and any(keyword in normalized for keyword in keywords):
            matched.append(intent)
    return matched


def classify_query(query: str) -> dict:
    """确定性意图分类。

    一条消息命中多个意图类别时，不再「首个关键词命中即返回」，而是按风险优先级
    仲裁出主意图（投诉 > 售后 > 订单 > 寒暄），并同时输出 ``intents``（全部命中）
    与 ``is_multi_intent``，供路由与人工交接摘要使用。

    提示注入攻击优先于一切规则：命中注入模式即归 ``complaint``（转人工）并打上
    ``security_flag="prompt_injection"``，阻断其进入知识问答/工具链路。
    """
    normalized = _normalize_query(query)
    # 注入优先：先于关键词与多意图仲裁，避免「忽略指令 + 查订单」被订单分支抢走。
    if _detect_injection(normalized):
        return {
            "intent": "complaint",
            "intent_confidence": 0.98,
            "slots": {},
            "intents": ["complaint"],
            "is_multi_intent": False,
            "security_flag": "prompt_injection",
        }
    matched = detect_intents(query)
    if matched:
        primary = max(matched, key=lambda i: _INTENT_PRIORITY.get(i, 0))
        return {
            "intent": primary,
            "intent_confidence": _confidence_for(primary),
            "slots": {},
            "intents": matched,
            "is_multi_intent": len(matched) > 1,
        }
    if len(normalized) >= 4:
        return {
            "intent": "knowledge_question",
            "intent_confidence": 0.75,
            "slots": {},
            "intents": ["knowledge_question"],
            "is_multi_intent": False,
        }
    return {
        "intent": "unknown",
        "intent_confidence": 0.35,
        "slots": {},
        "intents": ["unknown"],
        "is_multi_intent": False,
    }


def classify_intent(state: CustomerServiceState, classifier: Classifier | None = None) -> CustomerServiceState:
    query = state.get("current_query") or state.get("messages", [{}])[-1].get("content", "")
    explicit = (classifier or classify_query)(query)
    explicit_intent = explicit.get("intent")

    # 注入攻击优先：即便上一轮处于订单澄清，注入也立即归 complaint 转人工，不延续。
    if explicit.get("security_flag"):
        out = dict(state)
        out["intent"] = "complaint"
        out["intent_confidence"] = explicit.get("intent_confidence", 0.98)
        out["slots"] = {}
        out["intents"] = ["complaint"]
        out["is_multi_intent"] = False
        out["security_flag"] = explicit["security_flag"]
        out["current_query"] = query
        out["needs_clarification"] = False
        return out

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
        # 跨轮延续订单澄清：视为单意图，避免把上一轮的多意图信号带进来。
        intents = ["order_query"]
        is_multi = False
    else:
        intent = explicit_intent
        confidence = explicit.get("intent_confidence")
        # 全新意图：槽位从空开始，避免上一轮已结单的订单号残留。
        slots = {}
        intents = explicit.get("intents") or [intent]
        is_multi = bool(explicit.get("is_multi_intent", len(intents) > 1))

    out = dict(state)
    out["intent"] = intent
    out["intent_confidence"] = confidence
    out["slots"] = slots
    out["intents"] = intents
    out["is_multi_intent"] = is_multi
    out["security_flag"] = explicit.get("security_flag")
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
