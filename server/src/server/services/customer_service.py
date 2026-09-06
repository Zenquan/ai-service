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
import os
import re
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from server.graph.customer_service.classifier import classify_query
from server.services import rag
from server.services.chat_store import (
    HISTORY_LIMIT,
    ChatStore,
    MemoryChatStore,
    StoreUnavailable,
    build_default_store,
)
from server.tools.orders import extract_order_numbers

logger = logging.getLogger(__name__)

# 无登录阶段的演示身份：会话/订单归属校验默认用它，可经环境变量覆盖。
DEFAULT_USER_ID = os.getenv("DEMO_USER_ID", "demo-user")

_HANDOFF_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("投诉或高风险请求", ("投诉", "举报", "欺骗", "骗子", "赔偿", "人工客服")),
    ("业务工具尚未接入", ("订单", "物流", "快递", "配送", "发货", "收货", "退款", "退货", "换货", "售后", "维修")),
)

# 传给 LangGraph 的图状态快照只保留这些字段（messages 过大不入库，由消息表重建）。
_CHECKPOINT_FIELDS = ("intent", "intent_confidence", "slots", "citations", "citation_valid",
                      "response_mode", "needs_human", "needs_clarification",
                      "clarify_reason", "handoff_reason", "tool_calls", "tool_results")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handoff_reason(message: str) -> str | None:
    normalized = re.sub(r"\s+", "", message.lower())
    for reason, keywords in _HANDOFF_RULES:
        if any(keyword in normalized for keyword in keywords):
            return reason
    return None


def _prepare_checkpoint_restore(checkpoint: dict | None, message: str) -> dict | None:
    """仅在“确实在补上一轮业务槽位”时恢复图状态，避免把后续新问题锁死在澄清分支。"""
    if not checkpoint:
        return None
    if not (checkpoint.get("response_mode") == "clarify" and checkpoint.get("needs_clarification")):
        return None
    restored_intent = checkpoint.get("intent")
    # 目前只有订单工具需要多轮补槽位：新消息要么仍在问订单，要么提供了订单号候选。
    if restored_intent != "order_query":
        return None
    explicit_intent = classify_query(message).get("intent")
    continues_order = explicit_intent in {"order_query", "after_sale"} or bool(
        extract_order_numbers(message)
    )
    if not continues_order:
        return None
    return {
        "restored_intent": restored_intent,
        "restored_slots": dict(checkpoint.get("slots") or {}),
        "restored_needs_clarification": True,
        "clarify_reason": checkpoint.get("clarify_reason"),
    }


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

    def _ensure_conversation(
        self,
        conversation_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        """建会话时绑定归属；已有会话保持原归属（请求方身份另行传给图做工具校验）。"""
        try:
            conversation = self._active_store.get_or_create_conversation(
                conversation_id, user_id=user_id, tenant_id=tenant_id
            )
        except StoreUnavailable as exc:
            self._store_failed(exc)
            conversation = self._active_store.get_or_create_conversation(
                conversation_id, user_id=user_id, tenant_id=tenant_id
            )
        # 老会话可能还是空归属：首次带身份请求时补绑，便于工具做本人校验。
        if user_id and not conversation.get("user_id"):
            try:
                self._active_store.update_conversation(
                    conversation_id, user_id=user_id, tenant_id=tenant_id
                )
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.update_conversation(
                    conversation_id, user_id=user_id, tenant_id=tenant_id
                )
            conversation["user_id"] = user_id
            if tenant_id:
                conversation["tenant_id"] = tenant_id
        return conversation

    def _load_graph_restore(self, conversation_id: str, message: str) -> dict | None:
        try:
            checkpoint = self._active_store.load_checkpoint(conversation_id)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            checkpoint = self._active_store.load_checkpoint(conversation_id)
        return _prepare_checkpoint_restore(checkpoint, message)

    def _effective_user_id(self, conversation: dict, user_id: str | None) -> str:
        """身份优先级：请求显式身份 > 会话归属 > 演示默认。"""
        return user_id or conversation.get("user_id") or DEFAULT_USER_ID

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

    def _answer_with_graph(
        self,
        conversation_id: str,
        message: str,
        history: list[dict],
        user_id: str | None = None,
        tenant_id: str | None = None,
        initial_state: dict | None = None,
    ) -> dict | None:
        agent = self._get_graph_agent()
        if agent is None:
            return None
        # 历史里已包含本轮 user 消息时去掉尾部，agent.ask 会再补一次当前消息，避免重复。
        recent_history = list(history)
        if recent_history and recent_history[-1].get("content") == message:
            recent_history = recent_history[:-1]
        try:
            state = agent.ask(
                message,
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                history=recent_history,
                initial_state=initial_state,
            )
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
            "intent": state.get("intent"),
            "clarify_reason": state.get("clarify_reason"),
            "tool_results": state.get("tool_results", []),
            "tool_calls": state.get("tool_calls", []),
            "error": state.get("error"),
            "_graph_state": {
                key: state[key]
                for key in _CHECKPOINT_FIELDS
                if key in state
            },
        }

    def _save_graph_state(self, conversation_id: str, state: dict) -> None:
        if not state:
            return
        try:
            self._active_store.save_checkpoint(conversation_id, state)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            self._active_store.save_checkpoint(conversation_id, state)

    def handle_message(
        self,
        conversation_id: str,
        message: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        logger.info("客服消息收到 conversation_id=%s message_len=%d", conversation_id, len(message))
        with self._lock:
            conversation = self._ensure_conversation(conversation_id, user_id, tenant_id)
            effective_user_id = self._effective_user_id(conversation, user_id)

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

        restore = self._load_graph_restore(conversation_id, message)
        result = self._answer_with_graph(
            conversation_id,
            message,
            history,
            user_id=effective_user_id,
            tenant_id=tenant_id,
            initial_state=restore,
        )
        if result is None:
            # LangGraph 不可用时保留安全降级：投诉/售后/订单请求直接转人工，知识问答走 RAG。
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
        logger.info(
            "客服回复完成 conversation_id=%s response_mode=%s answer_len=%d handoff_reason=%s",
            conversation_id,
            result["response_mode"],
            len(result["answer"]),
            result.get("handoff_reason"),
        )
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
                    self._save_graph_state(conversation_id, graph_state)
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
                    self._save_graph_state(conversation_id, graph_state)

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

    def stream_message(
        self,
        conversation_id: str,
        message: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ):
        """流式消息（SSE 用）：业务/澄清/转人工走 LangGraph，知识问答保持流式 RAG。

        逐事件 yield dict（与 rag.ask_stream 同构 + meta/end 事件）：
          {"event": "meta", "response_mode", "handoff_reason", ...}   # 图结果（完整回答随 meta）
          {"event": "materials", "materials": [...]}
          {"event": "token", "text": "..."}
          {"event": "done", "answer", "citations", "citation_valid", "message_id"}
          {"event": "error", "error": "..."}
        """
        logger.info("客服流式消息收到 conversation_id=%s message_len=%d", conversation_id, len(message))
        with self._lock:
            conversation = self._ensure_conversation(conversation_id, user_id, tenant_id)
            effective_user_id = self._effective_user_id(conversation, user_id)

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

        restore = self._load_graph_restore(conversation_id, message)
        intent = classify_query(message).get("intent", "unknown")
        if restore is not None and restore.get("restored_intent"):
            intent = restore["restored_intent"]
        use_graph = self._get_graph_agent() is not None and (
            restore is not None or intent != "knowledge_question"
        )

        if use_graph:
            if intent == "order_query" and extract_order_numbers(message):
                yield {
                    "event": "progress",
                    "stage": "tool",
                    "tool": "query_order_status",
                    "label": "正在查询订单与物流…",
                }
            result = self._answer_with_graph(
                conversation_id,
                message,
                history,
                user_id=effective_user_id,
                tenant_id=tenant_id,
                initial_state=restore,
            )
            if result is not None:
                graph_state = result.pop("_graph_state", None)
                if graph_state is not None:
                    self._save_graph_state(conversation_id, graph_state)
                mode = result["response_mode"]
                answer = result["answer"]
                citations = result["citations"]
                if mode == "handoff":
                    logger.info(
                        "客服转人工 conversation_id=%s reason=%s",
                        conversation_id,
                        result.get("handoff_reason"),
                    )
                elif mode == "clarify":
                    logger.info(
                        "客服澄清 conversation_id=%s intent=%s",
                        conversation_id,
                        graph_state.get("intent") if graph_state else intent,
                    )
                yield {
                    "event": "meta",
                    "response_mode": mode,
                    "intent": result.get("intent") or intent,
                    "clarify_reason": result.get("clarify_reason"),
                    "needs_human": result["needs_human"],
                    "needs_clarification": result["needs_clarification"],
                    "handoff_reason": result.get("handoff_reason"),
                    "answer": answer,
                    "materials": result.get("materials", []),
                    "citations": citations,
                    "citation_valid": result["citation_valid"],
                    "tool_results": result.get("tool_results", []),
                }
                self._persist_assistant_message(
                    conversation_id,
                    answer,
                    result.get("materials", []),
                    mode,
                    result.get("handoff_reason"),
                    citations,
                )
                logger.info(
                    "客服流式图回复完成 conversation_id=%s response_mode=%s answer_len=%d handoff_reason=%s",
                    conversation_id,
                    mode,
                    len(answer),
                    result.get("handoff_reason"),
                )
                yield {"event": "done", "storage": self._storage_mode}
                return

        # 图不可用 / 图执行失败时的安全降级：业务请求直接转人工。
        handoff_reason = _handoff_reason(message)
        if handoff_reason:
            answer = "当前问题需要人工客服继续处理，我已为您准备转接。"
            logger.info(
                "客服转人工 conversation_id=%s reason=%s", conversation_id, handoff_reason
            )
            yield {
                "event": "meta",
                "response_mode": "handoff",
                "needs_human": True,
                "needs_clarification": False,
                "handoff_reason": handoff_reason,
                "answer": answer,
                "materials": [],
                "citations": [],
                "citation_valid": True,
            }
            self._persist_assistant_message(conversation_id, answer, [], "handoff", handoff_reason)
            yield {"event": "done", "storage": self._storage_mode}
            return

        # 知识问答：流式生成（暂不走 LangGraph 图——图内 generator 为同步全量接口；
        # 意图/澄清/工具等非知识分支已走图，后续图节点接入流式 generator 时整体切换）
        yield {
            "event": "progress",
            "stage": "rag",
            "label": "正在理解意图并检索知识库…",
        }
        answer_parts: list[str] = []
        materials: list[dict] = []
        final: dict | None = None
        error: str | None = None
        for event in rag.ask_stream(message):
            kind = event.get("event")
            if kind == "materials":
                materials = event["materials"]
                yield event
            elif kind == "token":
                answer_parts.append(event["text"])
                yield event
            elif kind == "done":
                final = event
            elif kind == "error":
                error = event["error"]
                yield event

        if error:
            # 生成失败：不落库 assistant 回复，由前端展示错误
            yield {"event": "done", "error": error, "storage": self._storage_mode}
            return

        answer = final["answer"] if final else "".join(answer_parts)
        citations = final["citations"] if final else []
        citation_valid = bool(final["citation_valid"]) if final else False
        logger.info(
            "客服流式回复完成 conversation_id=%s answer_len=%d citations=%s citation_valid=%s storage=%s",
            conversation_id,
            len(answer),
            citations,
            citation_valid,
            self._storage_mode,
        )
        self._persist_assistant_message(conversation_id, answer, materials, "answer", None, citations)
        yield {
            "event": "done",
            "answer": answer,
            "citations": citations,
            "citation_valid": citation_valid,
            "storage": self._storage_mode,
        }

    def _persist_assistant_message(
        self,
        conversation_id: str,
        answer: str,
        materials: list[dict],
        response_mode: str,
        handoff_reason: str | None,
        citations: list[int] | None = None,
    ) -> None:
        """落库 assistant 回复 + 更新会话状态（流式/非流式共用）。"""
        assistant_message = {
            "id": str(uuid4()),
            "role": "assistant",
            "content": answer,
            "created_at": _now(),
            "response_mode": response_mode,
            "citations": citations or [],
            "materials": materials,
            "handoff_reason": handoff_reason,
        }
        status = {"handoff": "handoff", "clarify": "waiting"}.get(response_mode, "open")
        with self._lock:
            try:
                self._active_store.append_message(conversation_id, assistant_message)
                self._active_store.update_conversation(
                    conversation_id,
                    status=status,
                    handoff_reason=handoff_reason,
                    updated_at=assistant_message["created_at"],
                )
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.append_message(conversation_id, assistant_message)
                self._active_store.update_conversation(
                    conversation_id,
                    status=status,
                    handoff_reason=handoff_reason,
                    updated_at=assistant_message["created_at"],
                )


customer_service = CustomerServiceService()
