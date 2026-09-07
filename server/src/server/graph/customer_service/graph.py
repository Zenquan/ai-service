"""客服 MVP LangGraph 图。"""
from __future__ import annotations

from typing import Callable

from langgraph.graph import END, START, StateGraph

from server.graph.customer_service.classifier import Classifier, classify_intent
from server.graph.customer_service.nodes import (
    Generator,
    Retriever,
    clarify,
    compose_answer,
    execute_order_tool,
    extract_slots,
    finalize_answer,
    handoff,
    load_session,
    retrieve_context,
    validate_answer,
)
from server.graph.customer_service.state import CustomerServiceState


def _guard_tool(order_tool, circuit_breaker):
    """用熔断器包装订单工具：open/half_open 拒绝，timeout/error 计入失败。

    ``not_found`` / ``forbidden`` 属业务语义（订单不存在/非本人），不计入熔断统计；
    只有服务级故障（``timeout`` / ``error``）会推进熔断状态机。
    """
    from server.tools.orders import ToolResult

    def guarded(raw_params: dict, requester_user_id: str) -> dict:
        name = "query_order_status"
        if not circuit_breaker.allow(name):
            return ToolResult(
                status="error",
                ok=False,
                message="订单服务熔断保护中，请稍后重试或转人工处理。",
                data={"circuit": "open"},
            ).to_dict()
        try:
            result = order_tool(raw_params, requester_user_id)
        except Exception:
            circuit_breaker.record_failure(name)
            raise
        status = result.get("status") if isinstance(result, dict) else "error"
        if status == "ok":
            circuit_breaker.record_success(name)
        elif status in {"timeout", "error"}:
            circuit_breaker.record_failure(name)
        return result

    return guarded


def _after_intent(state: CustomerServiceState) -> str:
    intent = state.get("intent")
    # 低置信度自适应追问：分类器（LLM）给出澄清文案时，优先追问而非硬路由，
    # 避免把含糊请求误判成知识问答/订单查询导致错误分支。
    if state.get("clarify_reason") and state.get("clarify_answer"):
        return "clarify"
    if intent == "knowledge_question":
        return "retrieve"
    if intent == "greeting":
        return "clarify"
    if intent == "order_query":
        return "extract_slots"
    if intent in {"after_sale", "complaint"}:
        return "handoff"
    return "clarify"


def _after_extract(state: CustomerServiceState) -> str:
    """订单槽位齐了走工具，缺单号走澄清（多轮补齐）。"""
    return "execute_order_tool" if (state.get("slots") or {}).get("order_no") else "clarify"


def _after_order_tool(state: CustomerServiceState) -> str:
    if state.get("needs_human"):
        return "handoff"
    if state.get("needs_clarification"):
        return "clarify"
    return "finalize"


def _after_validation(state: CustomerServiceState) -> str:
    return "handoff" if state.get("needs_human") else "finalize"


def build_customer_service_graph(
    retriever: Retriever | None = None,
    generator: Generator | None = None,
    classifier: Classifier | None = None,
    order_tool=None,
    circuit_breaker=None,
    checkpointer=None,
):
    """构建客服图，依赖全部通过参数注入，方便替换和测试。

    ``checkpointer``：LangGraph 原生 checkpointer（如 ``MysqlCheckpointSaver``），
    用于澄清/转人工中间态跨进程重启恢复；传入则按 ``thread_id``（conversation_id）
    持久化，不传则无持久化。

    ``circuit_breaker``：熔断器（``services/circuit_breaker.py``），传入后对订单工具
    调用做 closed/open/half-open 熔断保护；不传则工具调用不熔断（测试/降级）。
    """
    if order_tool is None:
        from server.tools.orders import query_order_status

        def order_tool(raw_params: dict, requester_user_id: str) -> dict:
            return query_order_status(raw_params, requester_user_id).to_dict()

    effective_tool = _guard_tool(order_tool, circuit_breaker) if circuit_breaker is not None else order_tool

    graph = StateGraph(CustomerServiceState)

    def classify_node(state: CustomerServiceState) -> CustomerServiceState:
        return classify_intent(state, classifier)

    def retrieve_node(state: CustomerServiceState) -> CustomerServiceState:
        return retrieve_context(state, retriever)

    def compose_node(state: CustomerServiceState) -> CustomerServiceState:
        return compose_answer(state, generator)

    def extract_node(state: CustomerServiceState) -> CustomerServiceState:
        return extract_slots(state)

    def order_tool_node(state: CustomerServiceState) -> CustomerServiceState:
        return execute_order_tool(state, effective_tool)

    graph.add_node("load_session", load_session)
    graph.add_node("classify_intent", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("compose_answer", compose_node)
    graph.add_node("validate_answer", validate_answer)
    graph.add_node("extract_slots", extract_node)
    graph.add_node("execute_order_tool", order_tool_node)
    graph.add_node("clarify", clarify)
    graph.add_node("handoff", handoff)
    graph.add_node("finalize", finalize_answer)

    graph.add_edge(START, "load_session")
    graph.add_edge("load_session", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        _after_intent,
        {
            "retrieve": "retrieve",
            "clarify": "clarify",
            "handoff": "handoff",
            "extract_slots": "extract_slots",
        },
    )
    graph.add_conditional_edges(
        "extract_slots",
        _after_extract,
        {"execute_order_tool": "execute_order_tool", "clarify": "clarify"},
    )
    graph.add_conditional_edges(
        "execute_order_tool",
        _after_order_tool,
        {"handoff": "handoff", "clarify": "clarify", "finalize": "finalize"},
    )
    graph.add_edge("retrieve", "compose_answer")
    graph.add_edge("compose_answer", "validate_answer")
    graph.add_conditional_edges(
        "validate_answer",
        _after_validation,
        {"handoff": "handoff", "finalize": "finalize"},
    )
    graph.add_edge("clarify", END)
    graph.add_edge("handoff", END)
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
