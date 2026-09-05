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
    finalize_answer,
    handoff,
    load_session,
    retrieve_context,
    validate_answer,
)
from server.graph.customer_service.state import CustomerServiceState


def _after_intent(state: CustomerServiceState) -> str:
    intent = state.get("intent")
    if intent == "knowledge_question":
        return "retrieve"
    if intent == "greeting":
        return "clarify"
    if intent in {"order_query", "after_sale", "complaint"}:
        return "handoff"
    return "clarify"


def _after_validation(state: CustomerServiceState) -> str:
    return "handoff" if state.get("needs_human") else "finalize"


def build_customer_service_graph(
    retriever: Retriever | None = None,
    generator: Generator | None = None,
    classifier: Classifier | None = None,
):
    """构建客服图，依赖全部通过参数注入，方便替换和测试。"""
    graph = StateGraph(CustomerServiceState)

    def classify_node(state: CustomerServiceState) -> CustomerServiceState:
        return classify_intent(state, classifier)

    def retrieve_node(state: CustomerServiceState) -> CustomerServiceState:
        return retrieve_context(state, retriever)

    def compose_node(state: CustomerServiceState) -> CustomerServiceState:
        return compose_answer(state, generator)

    graph.add_node("load_session", load_session)
    graph.add_node("classify_intent", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("compose_answer", compose_node)
    graph.add_node("validate_answer", validate_answer)
    graph.add_node("clarify", clarify)
    graph.add_node("handoff", handoff)
    graph.add_node("finalize", finalize_answer)

    graph.add_edge(START, "load_session")
    graph.add_edge("load_session", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        _after_intent,
        {"retrieve": "retrieve", "clarify": "clarify", "handoff": "handoff"},
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
    return graph.compile()
