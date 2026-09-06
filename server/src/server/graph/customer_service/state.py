"""客服流程的状态契约。

跨轮恢复语义（LangGraph 原生 checkpoint，见 services/checkpoint_saver.py）：
- ``messages`` 用 ``add_messages`` reducer 跨轮累加对话历史；
- ``intent`` / ``slots`` / ``needs_clarification`` 作为「会话级恢复信号」跨轮携带，
  由 ``classify_intent`` 决定是否延续上一轮的订单澄清；
- 其余字段（answer/citations/handoff 等）每轮由 ``load_session`` 重置，避免残留污染。
"""
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
    # 引用列表每轮由 validate_answer / execute_order_tool 赋值覆盖，不跨轮累加。
    citations: list[int]
    citation_valid: bool
    answer: str
    final_answer: str
    needs_clarification: bool
    needs_human: bool
    handoff_reason: str
    response_mode: Literal["answer", "clarify", "handoff"]
    rewrites: int
    error: str | None
    # 澄清与工具闭环（迭代 2）
    clarify_reason: str | None
    clarify_answer: str | None
    handoff_answer_override: str | None
    tool_calls: list[dict]
    tool_results: list[dict]
