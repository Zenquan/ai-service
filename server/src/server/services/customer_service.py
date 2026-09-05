"""智能客服会话服务：MySQL 会话持久化（回退内存）+ RAG 问答 + 安全转人工。

数据流：
- 会话与消息落库（MYSQL_HOST 配置且可用时走 MySQL，否则内存回退），服务重启不丢会话。
- 每轮把最近历史消息（chat_store.HISTORY_LIMIT 条）注入 LangGraph，实现多轮上下文。
- 图状态快照（意图/槽位/引用）保存到 graph_checkpoints，供后续迭代恢复。

规则显性化：
- 响应体带 ``storage`` 字段：mysql=持久化；memory=本地开发内存；memory_fallback=MySQL 不可用回退。
- 转人工规则仍为确定性关键词（见 _HANDOFF_RULES），LangGraph 图内同样可见该分支。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from server.services import rag
from server.services.chat_store import (
    HISTORY_LIMIT,
    ChatStore,
    MemoryChatStore,
    StoreUnavailable,
    build_default_store,
)

logger = logging.getLogger(__name__)


_HANDOFF_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("投诉或高风险请求", ("投诉", "举报", "欺骗", "骗子", "赔偿", "人工客服")),
    ("业务工具尚未接入", ("订单", "物流", "快递", "配送", "发货", "收货", "退款", "退货", "换货", "售后", "维修")),
)

# 传给 LangGraph 的图状态快照只保留这些字段（messages 过大不入库，由消息表重建）。
_CHECKPOINT_FIELDS = ("intent", "intent_confidence", "slots", "citations", "citation_valid",
                      "response_mode", "needs_human", "handoff_reason")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handoff_reason(message: str) -> str | None:
    normalized = re.sub(r"\s+", "", message.lower())
    for reason, keywords in _HANDOFF_RULES:
        if any(keyword in normalized for keyword in keywords):
            return reason
    return None


class CustomerServiceService:
    def __init__(self) -> None:
        self._fallback_store = MemoryChatStore()
        self._store, self._storage_mode = build_default_store()
        self._lock = Lock()
        self._graph_agent = None
        self._graph_checked = False

    @property
    def storage_mode(self) -> str:
        return self._storage_mode

    @property
    def _active_store(self) -> ChatStore:
        """当前可用存储：MySQL 失败后本进程内一直回退内存，避免每轮重试拖慢响应。"""
        return self._store

    def _store_failed(self, exc: StoreUnavailable) -> None:
        if self._storage_mode != "memory_fallback":
            logger.error("会话存储不可用，回退内存存储（重启后该会话数据不持久）: %s", exc)
            self._store = self._fallback_store
            self._storage_mode = "memory_fallback"

    def _get_graph_agent(self):
        if self._graph_checked:
            return self._graph_agent
        self._graph_checked = True
        try:
            from server.graph.customer_service import create_customer_service_agent

            self._graph_agent = create_customer_service_agent()
        except (ImportError, ModuleNotFoundError) as exc:
            logger.info("LangGraph agent unavailable; using RAG fallback: %s", exc)
            self._graph_agent = None
        except Exception:
            logger.exception("LangGraph agent initialization failed; using RAG fallback")
            self._graph_agent = None
        return self._graph_agent

    def _history_for_graph(self, conversation_id: str) -> list[dict]:
        """最近 HISTORY_LIMIT 条消息，转成 LangGraph 的 messages 列表（role/content）。"""
        try:
            messages = self._active_store.list_messages(conversation_id)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            messages = self._active_store.list_messages(conversation_id)
        return [
            {"role": message["role"], "content": message["content"]}
            for message in messages[-HISTORY_LIMIT:]
        ]

    def _answer_with_graph(self, conversation_id: str, message: str, history: list[dict]) -> dict | None:
        agent = self._get_graph_agent()
        if agent is None:
            return None
        try:
            state = agent.ask(message, conversation_id=conversation_id, history=history)
        except Exception:
            logger.exception("LangGraph agent request failed; using RAG fallback")
            return None
        answer = state.get("final_answer", "")
        contexts = state.get("contexts", [])
        return {
            "answer": answer,
            "materials": contexts,
            "citations": state.get("citations", []),
            "citation_valid": bool(state.get("citation_valid", True)),
            "response_mode": state.get("response_mode", "answer"),
            "needs_human": bool(state.get("needs_human", False)),
            "needs_clarification": bool(state.get("needs_clarification", False)),
            "handoff_reason": state.get("handoff_reason"),
            "error": state.get("error"),
            "_graph_state": {
                key: state[key]
                for key in _CHECKPOINT_FIELDS
                if key in state
            },
        }

    def handle_message(self, conversation_id: str, message: str) -> dict:
        with self._lock:
            try:
                self._active_store.get_or_create_conversation(conversation_id)
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.get_or_create_conversation(conversation_id)

            user_message = {
                "id": str(uuid4()),
                "role": "user",
                "content": message,
                "created_at": _now(),
            }
            try:
                self._active_store.append_message(conversation_id, user_message)
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.append_message(conversation_id, user_message)

            history = self._history_for_graph(conversation_id)

        handoff_reason = _handoff_reason(message)
        if handoff_reason:
            result = {
                "answer": "当前问题需要人工客服继续处理，我已为您准备转接。",
                "materials": [],
                "citations": [],
                "citation_valid": True,
                "response_mode": "handoff",
                "needs_human": True,
                "needs_clarification": False,
                "handoff_reason": handoff_reason,
                "error": None,
                "_graph_state": None,
            }
        else:
            result = self._answer_with_graph(conversation_id, message, history)
            if result is None:
                rag_result = rag.ask(message)
                answer = rag_result.get("answer", "")
                error = rag_result.get("error")
                result = {
                    "answer": answer,
                    "materials": rag_result.get("materials", []),
                    "citations": rag_result.get("citations", []),
                    "citation_valid": bool(rag_result.get("citation_valid", False)),
                    "response_mode": "answer" if answer and not error else "handoff",
                    "needs_human": bool(error),
                    "needs_clarification": not answer and not error,
                    "handoff_reason": "知识库服务暂时不可用" if error else "知识库未返回可用回答" if not answer else None,
                    "error": error,
                    "_graph_state": None,
                }

        graph_state = result.pop("_graph_state", None)
        assistant_message = {
            "id": str(uuid4()),
            "role": "assistant",
            "content": result["answer"],
            "created_at": _now(),
            "response_mode": result["response_mode"],
            "citations": result["citations"],
            "materials": result.get("materials", []),
        }

        with self._lock:
            try:
                self._active_store.append_message(conversation_id, assistant_message)
                status = {
                    "handoff": "handoff",
                    "clarify": "waiting",
                }.get(result["response_mode"], "open")
                self._active_store.update_conversation(
                    conversation_id,
                    status=status,
                    handoff_reason=result.get("handoff_reason"),
                    updated_at=assistant_message["created_at"],
                )
                if graph_state is not None:
                    self._active_store.save_checkpoint(conversation_id, graph_state)
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.append_message(conversation_id, assistant_message)
                self._active_store.update_conversation(
                    conversation_id,
                    status={"handoff": "handoff", "clarify": "waiting"}.get(result["response_mode"], "open"),
                    handoff_reason=result.get("handoff_reason"),
                    updated_at=assistant_message["created_at"],
                )
                if graph_state is not None:
                    self._active_store.save_checkpoint(conversation_id, graph_state)

        return {
            "conversation_id": conversation_id,
            "message_id": assistant_message["id"],
            "storage": self._storage_mode,
            **result,
        }

    def get_conversation(self, conversation_id: str) -> dict:
        try:
            conversation = self._active_store.get_or_create_conversation(conversation_id)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            conversation = self._active_store.get_or_create_conversation(conversation_id)
        try:
            message_count = len(self._active_store.list_messages(conversation_id))
        except StoreUnavailable as exc:
            self._store_failed(exc)
            message_count = len(self._active_store.list_messages(conversation_id))
        return {
            "conversation_id": conversation["id"],
            "created_at": conversation["created_at"],
            "updated_at": conversation["updated_at"],
            "message_count": message_count,
            "status": conversation["status"],
            "handoff_reason": conversation.get("handoff_reason"),
            "storage": self._storage_mode,
        }

    def get_messages(self, conversation_id: str) -> list[dict]:
        try:
            return self._active_store.list_messages(conversation_id)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            return self._active_store.list_messages(conversation_id)

    def list_conversations(self, limit: int = 50) -> list[dict]:
        try:
            conversations = self._active_store.list_conversations(limit)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            conversations = self._active_store.list_conversations(limit)
        return [
            {**conversation, "storage": self._storage_mode}
            for conversation in conversations
        ]


customer_service = CustomerServiceService()
