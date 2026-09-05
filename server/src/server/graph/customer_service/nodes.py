"""客服 MVP 图节点。"""
from __future__ import annotations

import re
from typing import Callable

from server.graph.customer_service.state import CustomerServiceState

Retriever = Callable[[str, int], list[dict]]
Generator = Callable[[str, list[dict], list], tuple[str, list[int]]]


def load_session(state: CustomerServiceState) -> CustomerServiceState:
    """初始化图状态；正式环境由 checkpoint/session repository 提供历史。"""
    out = dict(state)
    out.setdefault("contexts", [])
    out.setdefault("citations", [])
    out.setdefault("rewrites", 0)
    out.setdefault("needs_human", False)
    out.setdefault("needs_clarification", False)
    out.setdefault("slots", {})
    return out


def retrieve_context(state: CustomerServiceState, retriever: Retriever | None) -> CustomerServiceState:
    if retriever is None:
        out = dict(state)
        out["contexts"] = []
        out["error"] = "RAG 检索器未配置"
        return out
    query = state.get("current_query", "")
    contexts = retriever(query, top_k=5)
    out = dict(state)
    out["contexts"] = [dict(context) for context in contexts]
    return out


def compose_answer(state: CustomerServiceState, generator: Generator | None) -> CustomerServiceState:
    if generator is None:
        out = dict(state)
        out["answer"] = "暂时无法生成回答，请转人工处理。"
        out["needs_human"] = True
        out["handoff_reason"] = "生成器未配置"
        return out
    answer, citations = generator(
        state.get("current_query", ""),
        state.get("contexts", []),
        state.get("messages", []),
    )
    out = dict(state)
    out["answer"] = answer
    out["citations"] = citations
    return out


def validate_answer(state: CustomerServiceState) -> CustomerServiceState:
    answer = state.get("answer", "")
    contexts = state.get("contexts", [])
    cited = set(state.get("citations", [])) or {
        int(match) for match in re.findall(r"\[(?:来源)?(\d+)\]", answer)
    }
    valid = cited.issubset(set(range(1, len(contexts) + 1)))
    out = dict(state)
    out["citations"] = sorted(cited)
    out["citation_valid"] = valid
    if not valid:
        out["needs_human"] = True
        out["handoff_reason"] = "回答引用校验失败"
    return out


def clarify(state: CustomerServiceState) -> CustomerServiceState:
    out = dict(state)
    out.update({
        "needs_clarification": True,
        "response_mode": "clarify",
        "final_answer": "请补充您需要咨询的产品、订单或售后问题，我再帮您处理。",
    })
    return out


def handoff(state: CustomerServiceState, reason: str | None = None) -> CustomerServiceState:
    out = dict(state)
    handoff_reason = reason or state.get("handoff_reason")
    if not handoff_reason:
        handoff_reason = (
            "业务工具尚未接入"
            if state.get("intent") in {"order_query", "after_sale"}
            else "客服流程需要人工介入"
        )
    out.update({
        "needs_human": True,
        "response_mode": "handoff",
        "handoff_reason": handoff_reason,
        "final_answer": (
            "当前暂不能直接查询或办理该业务，我已为您准备转接人工客服。"
            if handoff_reason == "业务工具尚未接入"
            else "当前问题需要人工客服继续处理，我已为您准备转接。"
        ),
    })
    return out


def finalize_answer(state: CustomerServiceState) -> CustomerServiceState:
    out = dict(state)
    if state.get("needs_human"):
        return handoff(out)
    out["response_mode"] = "answer"
    out["final_answer"] = state.get("answer", "")
    return out
