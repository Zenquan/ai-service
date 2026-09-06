"""智能客服会话服务：MySQL 会话持久化（回退内存）+ RAG 问答 + 安全转人工。

数据流：
- 会话与消息落库（MYSQL_HOST 配置且可用时走 MySQL，否则内存回退），服务重启不丢会话。
- 每轮消息交给 LangGraph 客服图；多轮上下文与澄清/转人工中间态由 LangGraph 原生
  checkpointer（services/checkpoint_saver.py）按 thread_id=conversation_id 持久化，
  MySQL 可用时跨进程重启恢复，否则回退进程内 InMemorySaver。
- 知识库「无素材」的澄清轮次计数（clarify_round）仍落 graph_checkpoints（服务层局部状态）。

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
    ChatStore,
    MemoryChatStore,
    StoreUnavailable,
    build_default_store,
)
from server.services.checkpoint_saver import build_checkpointer
from server.services.metrics import EvaluationRecorder, build_default_recorder
from server.tools.orders import extract_order_numbers

logger = logging.getLogger(__name__)

# 无登录阶段的演示身份：会话/订单归属校验默认用它，可经环境变量覆盖。
DEFAULT_USER_ID = os.getenv("DEMO_USER_ID", "demo-user")

_HANDOFF_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("投诉或高风险请求", ("投诉", "举报", "欺骗", "骗子", "赔偿", "人工客服")),
    ("业务工具尚未接入", ("订单", "物流", "快递", "配送", "发货", "收货", "退款", "退货", "换货", "售后", "维修")),
)

# 传给 LangGraph 的图状态由原生 checkpointer（MysqlCheckpointSaver / InMemorySaver）
# 以 thread_id=conversation_id 持久化，见 services/checkpoint_saver.py；不再手写快照。


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _handoff_reason(message: str) -> str | None:
    normalized = re.sub(r"\s+", "", message.lower())
    for reason, keywords in _HANDOFF_RULES:
        if any(keyword in normalized for keyword in keywords):
            return reason
    return None


def _use_llm_classifier() -> bool:
    """是否启用 LLM 意图分类器。

    默认关闭（走确定性规则分类器，保证零外部依赖与稳定契约）；
    设 ``LLM_CLASSIFIER=1`` 且配置了 ``DEEPSEEK_API_KEY`` 时启用，
    LLM 不可用会自动回退规则分类器（见 llm_classifier.LLMIntentClassifier）。
    """
    return os.getenv("LLM_CLASSIFIER", "").strip() in {"1", "true", "yes", "on"}


def _first_tool_status(tool_results: list[dict]) -> str | None:
    """从 ``tool_results`` 提取首个调用的状态（ok / not_found / forbidden / timeout / error）。

    无工具调用或结果为空时返回 ``None``，评测聚合层会把 ``None`` 视为 ``no_tool``。
    """
    if not tool_results:
        return None
    first = tool_results[0] or {}
    status = first.get("status") or first.get("ok")
    if isinstance(status, bool):
        return "ok" if status else "error"
    return str(status) if status else None


class CustomerServiceService:
    def __init__(self) -> None:
        self._fallback_store = MemoryChatStore()
        self._store, self._storage_mode = build_default_store()
        self._lock = Lock()
        self._graph_agent = None
        self._graph_checked = False
        # 评测指标埋点器：与 chat_store 同款，无 MySQL 回退内存；评测不应阻塞主流程。
        self._recorder = build_default_recorder()

    @property
    def storage_mode(self) -> str:
        return self._storage_mode

    @property
    def recorder(self) -> "EvaluationRecorder":
        """评测指标埋点器（供 Prometheus 暴露层拉取事件聚合）。"""
        return self._recorder

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
            from server.graph.customer_service import (
                build_llm_classifier,
                create_customer_service_agent,
            )

            classifier = None
            if _use_llm_classifier():
                classifier = build_llm_classifier()
            self._graph_agent = create_customer_service_agent(
                classifier=classifier,
                checkpointer=build_checkpointer(),
            )
        except (ImportError, ModuleNotFoundError) as exc:
            logger.info("LangGraph agent unavailable; using RAG fallback: %s", exc)
            self._graph_agent = None
        except Exception:
            logger.exception("LangGraph agent initialization failed; using RAG fallback")
            self._graph_agent = None
        return self._graph_agent

    def _graph_mid_clarify(self, conversation_id: str, message: str) -> bool:
        """会话是否处于「订单澄清待补充」且本轮消息确实在延续该澄清。

        仅当上一轮为订单澄清（intent=order_query + needs_clarification）且本轮仍在补订单
        信息（含订单关键词或订单号候选）时返回 True，避免把后续新问题锁进订单/检索链路。
        """
        agent = self._get_graph_agent()
        if agent is None:
            return False
        state = agent.get_state(conversation_id)
        if not (state and state.get("intent") == "order_query" and state.get("needs_clarification")):
            return False
        explicit_intent = classify_query(message).get("intent")
        return explicit_intent in {"order_query", "after_sale"} or bool(
            extract_order_numbers(message)
        )

    def _answer_with_graph(
        self,
        conversation_id: str,
        message: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        message_id: str | None = None,
    ) -> dict | None:
        agent = self._get_graph_agent()
        if agent is None:
            return None
        import time as _time

        started = _time.monotonic()
        try:
            state = agent.ask(
                message,
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        except Exception:
            logger.exception("LangGraph agent request failed; using RAG fallback")
            self._record_metrics(
                conversation_id=conversation_id,
                message_id=message_id,
                payload={"error": "graph_exception"},
                latency_ms=int((_time.monotonic() - started) * 1000),
            )
            return None
        latency_ms = int((_time.monotonic() - started) * 1000)
        answer = state.get("final_answer", "")
        contexts = state.get("contexts", [])
        result = {
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
        }
        self._record_metrics(
            conversation_id=conversation_id,
            message_id=message_id,
            payload={
                "intent": result["intent"],
                "response_mode": result["response_mode"],
                "citation_valid": result["citation_valid"],
                "needs_human": result["needs_human"],
                "needs_clarification": result["needs_clarification"],
                "tool_status": _first_tool_status(result["tool_results"]),
                "tool_calls_count": len(result["tool_calls"]),
                "materials_count": len(contexts),
                "citations_count": len(result["citations"]),
                "error": result["error"],
                "clarify_reason": result["clarify_reason"],
                "handoff_reason": result["handoff_reason"],
            },
            latency_ms=latency_ms,
        )
        return result

    def _record_metrics(
        self,
        conversation_id: str,
        message_id: str | None,
        payload: dict,
        latency_ms: int | None = None,
    ) -> None:
        """统一的评测埋点入口：记录 trace_id / latency 等元数据后再写入。

        任何异常吞掉——评测埋点不应阻塞业务主流程。
        """
        try:
            from server.observability import get_trace_id

            trace_id = get_trace_id()
            record_payload = dict(payload)
            if trace_id:
                record_payload["trace_id"] = trace_id
            if latency_ms is not None:
                record_payload["latency_ms"] = latency_ms
            record_payload["conversation_id"] = conversation_id
            if message_id:
                record_payload["message_id"] = message_id
            self._recorder.record_event(record_payload)
        except Exception:  # noqa: BLE001
            logger.debug("metrics record skipped", exc_info=True)

    def _save_graph_state(self, conversation_id: str, state: dict) -> None:
        if not state:
            return
        try:
            self._active_store.save_checkpoint(conversation_id, state)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            self._active_store.save_checkpoint(conversation_id, state)

    def _resolve_no_material(
        self,
        conversation_id: str,
        reason: str,
    ) -> dict:
        """第三层兜底：知识库无素材时，第一次澄清并给明确去向，第二次仍无解则转人工。"""
        try:
            checkpoint = self._active_store.load_checkpoint(conversation_id)
        except StoreUnavailable as exc:
            self._store_failed(exc)
            checkpoint = self._active_store.load_checkpoint(conversation_id)
        previous_round = int((checkpoint or {}).get("clarify_round") or 0)

        if previous_round >= 1:
            reason_text = (
                "知识库还没有文档，连续两轮都无法给出有依据的回答。"
                if reason == "empty_kb"
                else "这个问题在知识库中连续两轮没有找到足够相关的资料。"
            )
            return {
                "result": {
                    "answer": f"{reason_text}我已为您转人工处理，请稍候。",
                    "materials": [],
                    "citations": [],
                    "citation_valid": True,
                    "response_mode": "handoff",
                    "needs_human": True,
                    "needs_clarification": False,
                    "handoff_reason": "知识库无法回答且澄清无进展",
                    "intent": "knowledge_question",
                    "clarify_reason": None,
                    "tool_results": [],
                    "tool_calls": [],
                    "error": None,
                },
                "graph_state": {
                    "intent": "knowledge_question",
                    "response_mode": "handoff",
                    "needs_human": True,
                    "needs_clarification": False,
                    "handoff_reason": "知识库无法回答且澄清无进展",
                    "clarify_round": previous_round + 1,
                    "citations": [],
                    "citation_valid": True,
                    "slots": {},
                },
            }

        if reason == "empty_kb":
            clarify_reason = (
                "知识库还没有文档，暂时无法基于资料回答。"
                "您可以先上传资料，或直接说“转人工”让客服介入。"
            )
        else:
            clarify_reason = (
                "这个问题在知识库中没有找到足够相关的资料。"
                "您可以换个说法、补充具体名称；如果确认需要人工帮助，请直接说“转人工”。"
            )
        return {
            "result": {
                "answer": clarify_reason,
                "materials": [],
                "citations": [],
                "citation_valid": True,
                "response_mode": "clarify",
                "needs_human": False,
                "needs_clarification": True,
                "handoff_reason": None,
                "intent": "knowledge_question",
                "clarify_reason": clarify_reason,
                "tool_results": [],
                "tool_calls": [],
                "error": None,
            },
            "graph_state": {
                "intent": "knowledge_question",
                "response_mode": "clarify",
                "needs_human": False,
                "needs_clarification": True,
                "clarify_reason": clarify_reason,
                "clarify_round": previous_round + 1,
                "citations": [],
                "citation_valid": True,
                "slots": {},
            },
        }

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

        if conversation.get("status") in {"handoff", "manual"}:
            # 人工接管/处理中的会话：客户新消息只入队，不进入 AI 链路。
            answer = "您的消息已收到，人工坐席会继续为您处理。"
            try:
                self._active_store.update_conversation(
                    conversation_id,
                    status="handoff",
                    handoff_reason="客户在人工会话中补充消息",
                    updated_at=user_message["created_at"],
                )
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.update_conversation(
                    conversation_id,
                    status="handoff",
                    handoff_reason="客户在人工会话中补充消息",
                    updated_at=user_message["created_at"],
                )
            logger.info(
                "人工会话收到客户新消息 conversation_id=%s message_len=%d",
                conversation_id,
                len(message),
            )
            return {
                "conversation_id": conversation_id,
                "message_id": user_message["id"],
                "storage": self._storage_mode,
                "answer": answer,
                "materials": [],
                "citations": [],
                "citation_valid": True,
                "response_mode": "handoff",
                "needs_human": True,
                "needs_clarification": False,
                "handoff_reason": "客户在人工会话中补充消息",
                "error": None,
            }

        result = self._answer_with_graph(
            conversation_id,
            message,
            user_id=effective_user_id,
            tenant_id=tenant_id,
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
                }

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
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.append_message(conversation_id, assistant_message)
                self._active_store.update_conversation(
                    conversation_id,
                    status={"handoff": "handoff", "clarify": "waiting"}.get(result["response_mode"], "open"),
                    handoff_reason=result.get("handoff_reason"),
                    updated_at=assistant_message["created_at"],
                )

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

        if conversation.get("status") in {"handoff", "manual"}:
            # 人工接管/处理中的会话：客户新消息只入队，不进入 AI 链路（SSE 端用 meta 提示）。
            answer = "您的消息已收到，人工坐席会继续为您处理。"
            try:
                self._active_store.update_conversation(
                    conversation_id,
                    status="handoff",
                    handoff_reason="客户在人工会话中补充消息",
                    updated_at=user_message["created_at"],
                )
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.update_conversation(
                    conversation_id,
                    status="handoff",
                    handoff_reason="客户在人工会话中补充消息",
                    updated_at=user_message["created_at"],
                )
            logger.info(
                "人工会话流式收到客户新消息 conversation_id=%s message_len=%d",
                conversation_id,
                len(message),
            )
            yield {
                "event": "meta",
                "response_mode": "handoff",
                "intent": "unknown",
                "clarify_reason": None,
                "needs_human": True,
                "needs_clarification": False,
                "handoff_reason": "客户在人工会话中补充消息",
                "answer": answer,
                "materials": [],
                "citations": [],
                "citation_valid": True,
                "tool_results": [],
            }
            yield {"event": "done", "storage": self._storage_mode}
            return

        mid_clarify = self._graph_mid_clarify(conversation_id, message)
        intent = classify_query(message).get("intent", "unknown")
        if mid_clarify:
            intent = "order_query"
        use_graph = self._get_graph_agent() is not None and (
            mid_clarify or intent != "knowledge_question"
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
                user_id=effective_user_id,
                tenant_id=tenant_id,
            )
            if result is not None:
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
                        result.get("intent") or intent,
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
            fallback_intent = classify_query(message).get("intent")
            logger.info(
                "客服转人工 conversation_id=%s reason=%s", conversation_id, handoff_reason
            )
            yield {
                "event": "meta",
                "response_mode": "handoff",
                "intent": fallback_intent,
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
        no_material_reason: str | None = None
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
            elif kind == "no_material":
                no_material_reason = event.get("reason", "no_relevant")
                break
            elif kind == "error":
                error = event["error"]
                yield event

        if no_material_reason:
            resolved = self._resolve_no_material(conversation_id, no_material_reason)
            result = resolved["result"]
            graph_state = resolved["graph_state"]
            self._save_graph_state(conversation_id, graph_state)
            mode = result["response_mode"]
            answer = result["answer"]
            if mode == "handoff":
                logger.info(
                    "客服转人工 conversation_id=%s reason=%s",
                    conversation_id,
                    result.get("handoff_reason"),
                )
            else:
                logger.info(
                    "客服澄清 conversation_id=%s reason=%s",
                    conversation_id,
                    result.get("clarify_reason"),
                )
            yield {
                "event": "meta",
                "response_mode": mode,
                "intent": "knowledge_question",
                "clarify_reason": result.get("clarify_reason"),
                "needs_human": result["needs_human"],
                "needs_clarification": result["needs_clarification"],
                "handoff_reason": result.get("handoff_reason"),
                "answer": answer,
                "materials": [],
                "citations": [],
                "citation_valid": True,
                "tool_results": [],
            }
            self._persist_assistant_message(
                conversation_id,
                answer,
                [],
                mode,
                result.get("handoff_reason"),
                [],
            )
            yield {"event": "done", "storage": self._storage_mode}
            return

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

    def send_manual_reply(
        self,
        conversation_id: str,
        message: str,
        agent_name: str = "Zenquan",
    ) -> dict:
        """人工坐席回复：仅对已转人工（或已在人工处理中）的会话开放。

        消息以 ``response_mode=manual`` 落库，会话状态更新为 ``manual``，
        避免与 AI 自动回复混在一起。
        """
        logger.info(
            "人工回复 conversation_id=%s agent=%s message_len=%d",
            conversation_id,
            agent_name,
            len(message),
        )
        conversation = self.get_conversation(conversation_id)
        if conversation.get("status") not in {"handoff", "manual"}:
            raise ValueError(
                f"会话当前状态为 {conversation.get('status')}，只有转人工后可人工回复"
            )

        reply = {
            "id": str(uuid4()),
            "role": "assistant",
            "content": message,
            "created_at": _now(),
            "response_mode": "manual",
            "citations": [],
            "materials": [],
            "needs_human": False,
            "handoff_reason": None,
        }
        with self._lock:
            try:
                self._active_store.append_message(conversation_id, reply)
                self._active_store.update_conversation(
                    conversation_id,
                    status="manual",
                    handoff_reason=None,
                    updated_at=reply["created_at"],
                )
            except StoreUnavailable as exc:
                self._store_failed(exc)
                self._active_store.append_message(conversation_id, reply)
                self._active_store.update_conversation(
                    conversation_id,
                    status="manual",
                    handoff_reason=None,
                    updated_at=reply["created_at"],
                )
        return {
            "conversation_id": conversation_id,
            "message_id": reply["id"],
            "storage": self._storage_mode,
            "response_mode": "manual",
            "agent_name": agent_name,
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
