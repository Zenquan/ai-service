"""客服流程的状态契约。"""
from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

Intent = Literal[
    "greeting",
    "knowledge_question",
    "order_query",
    "after_sale",
    "complaint",
    "unknown",
]


def _merge_unique(left: list | None, right: list | None) -> list:
    values: list = []
    for value in (left or []) + (right or []):
        if value not in values:
            values.append(value)
    return values


class CustomerServiceState(TypedDict, total=False):
    conversation_id: str
    user_id: str | None
    tenant_id: str | None
    messages: Annotated[list, add_messages]
    current_query: str
    intent: Intent
    intent_confidence: float
    slots: dict
    contexts: list[dict]
    citations: Annotated[list[int], _merge_unique]
    citation_valid: bool
    answer: str
    final_answer: str
    needs_clarification: bool
    needs_human: bool
    handoff_reason: str
    response_mode: Literal["answer", "clarify", "handoff"]
    rewrites: int
    error: str | None
