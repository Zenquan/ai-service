"""客服 MVP 图节点。"""
from __future__ import annotations

import re
from typing import Callable

from server.tools.orders import ToolResult, extract_order_numbers
from server.graph.customer_service.state import CustomerServiceState

Retriever = Callable[[str, int], list[dict]]
Generator = Callable[[str, list[dict], list], tuple[str, list[int]]]
OrderTool = Callable[..., dict]


def load_session(state: CustomerServiceState) -> CustomerServiceState:
    """初始化图状态；正式环境由 checkpoint/session repository 提供历史。"""
    out = dict(state)
    out.setdefault("contexts", [])
    out.setdefault("citations", [])
    out.setdefault("rewrites", 0)
    out.setdefault("needs_human", False)
    out.setdefault("needs_clarification", False)
    out.setdefault("slots", {})
    out.setdefault("clarify_reason", None)
    out.setdefault("tool_calls", [])
    out.setdefault("tool_results", [])
    out.setdefault("restored_slots", {})
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


def extract_slots(state: CustomerServiceState) -> CustomerServiceState:
    """从当前消息中补槽位（迭代 2 目前只处理 order_query 的订单号）。"""
    out = dict(state)
    query = state.get("current_query", "")
    slots = dict(state.get("slots", {}))
    if state.get("intent") == "order_query":
        candidates = extract_order_numbers(query)
        if candidates:
            slots["order_no"] = candidates[0]
        else:
            out["clarify_reason"] = "请提供您的订单号（如 A00001），我再帮您查询物流状态。"
    out["slots"] = slots
    return out


def _order_answer_text(data: dict | None) -> str:
    if not data:
        return "订单查询成功。"
    timeline = data.get("timeline") or []
    lines = [
        f"您的订单 {data.get('order_no', '')} 当前状态：**{data.get('status', '')}**。",
    ]
    if data.get("carrier") and data.get("tracking_no"):
        lines.append(f"承运方：{data['carrier']}，单号：{data['tracking_no']}。")
    if timeline:
        latest = timeline[-1]
        lines.append(f"最新动态（{latest.get('time', '')}）：{latest.get('event', '')}。")
    else:
        lines.append("暂无可展示的物流轨迹。")
    return "\n\n".join(lines)


def execute_order_tool(state: CustomerServiceState, order_tool: OrderTool | None) -> CustomerServiceState:
    """执行只读订单工具：按 ToolResult 显式分流，不把失败改写成成功。"""
    out = dict(state)
    out["tool_calls"] = list(state.get("tool_calls", []))
    out["tool_results"] = list(state.get("tool_results", []))
    slots = dict(state.get("slots", {}))
    out["slots"] = slots

    order_no = slots.get("order_no")
    requester_user_id = state.get("user_id") or ""
    out["tool_calls"].append({
        "tool": "query_order_status",
        "args": {"order_no": order_no} if order_no else {},
        "requester_user_id": requester_user_id,
    })

    if order_tool is None:
        out.update({
            "needs_human": True,
            "response_mode": "handoff",
            "handoff_reason": "订单工具未配置",
            "handoff_answer_override": "当前订单查询工具不可用，我已为您转人工处理。",
        })
        return out

    try:
        raw = order_tool({"order_no": order_no}, requester_user_id)
        result = ToolResult(**raw) if isinstance(raw, dict) else raw
    except Exception as exc:  # noqa: BLE001 —— 工具抛异常按失败处理
        result = ToolResult(status="error", message=f"订单工具异常：{exc}")

    out["tool_results"].append(result.to_dict())
    if result.status == "ok":
        out.update({
            "needs_human": False,
            "needs_clarification": False,
            "response_mode": "answer",
            "answer": _order_answer_text(result.data),
            "citations": [],
            "citation_valid": True,
            "error": None,
        })
        return out

    if result.status == "not_found":
        reason = "订单不存在或订单号有误"
        override = result.message
    elif result.status == "forbidden":
        reason = "非本人订单，权限拒绝"
        override = result.message
    elif result.status == "timeout":
        reason = "订单服务超时"
        override = "订单查询超时，已为您转人工处理，请稍候。"
    else:
        reason = "订单服务暂不可用"
        override = "订单服务暂不可用，已为您转人工处理。"
    out.update({
        "needs_human": True,
        "needs_clarification": False,
        "response_mode": "handoff",
        "handoff_reason": reason,
        "handoff_answer_override": override,
        "error": reason,
    })
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
    reason = state.get("clarify_reason")
    out.update({
        "needs_clarification": True,
        "response_mode": "clarify",
        "clarify_reason": reason,
        "final_answer": state.get("clarify_answer")
        or reason
        or "请补充您需要咨询的产品、订单或售后问题，我再帮您处理。",
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
        "final_answer": state.get("handoff_answer_override") or (
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
