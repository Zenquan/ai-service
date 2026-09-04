"""智能客服会话服务：MVP 内存会话 + RAG 问答 + 安全转人工。"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.services import rag

logger = logging.getLogger(__name__)


_HANDOFF_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("投诉或高风险请求", ("投诉", "举报", "欺骗", "骗子", "赔偿", "人工客服")),
    ("业务工具尚未接入", ("订单", "物流", "快递", "配送", "发货", "收货", "退款", "退货", "换货", "售后", "维修")),
)


@dataclass
class Conversation:
    conversation_id: str
    created_at: str
    updated_at: str
    messages: list[dict] = field(default_factory=list)
    status: str = "open"
    handoff_reason: str | None = None


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
        self._conversations: dict[str, Conversation] = {}
        self._lock = Lock()
        self._graph_agent = None
        self._graph_checked = False

    def _get_graph_agent(self):
        if self._graph_checked:
            return self._graph_agent
        self._graph_checked = True
        graph_src = Path(__file__).resolve().parents[2] / "langgraph" / "src"
        if str(graph_src) not in sys.path:
            sys.path.insert(0, str(graph_src))
        try:
            from langgraph_graph.customer_service import create_customer_service_agent

            self._graph_agent = create_customer_service_agent()
        except (ImportError, ModuleNotFoundError) as exc:
            logger.info("LangGraph agent unavailable; using RAG fallback: %s", exc)
            self._graph_agent = None
        except Exception:
            logger.exception("LangGraph agent initialization failed; using RAG fallback")
            self._graph_agent = None
        return self._graph_agent

    def _answer_with_graph(self, conversation_id: str, message: str) -> dict | None:
        agent = self._get_graph_agent()
        if agent is None:
            return None
        try:
            state = agent.ask(message, conversation_id=conversation_id)
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
        }

    def _get_or_create(self, conversation_id: str) -> Conversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            timestamp = _now()
            conversation = Conversation(conversation_id, timestamp, timestamp)
            self._conversations[conversation_id] = conversation
        return conversation

    def handle_message(self, conversation_id: str, message: str) -> dict:
        with self._lock:
            conversation = self._get_or_create(conversation_id)
            user_message = {
                "id": str(uuid4()),
                "role": "user",
                "content": message,
                "created_at": _now(),
            }
            conversation.messages.append(user_message)

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
            result = self._answer_with_graph(conversation_id, message)
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
                }

        assistant_message = {
            "id": str(uuid4()),
            "role": "assistant",
            "content": result["answer"],
            "created_at": _now(),
            "response_mode": result["response_mode"],
            "citations": result["citations"],
        }
        with self._lock:
            conversation.messages.append(assistant_message)
            conversation.updated_at = assistant_message["created_at"]
            conversation.status = {
                "handoff": "handoff",
                "clarify": "waiting",
            }.get(result["response_mode"], "open")
            conversation.handoff_reason = result.get("handoff_reason")

        return {
            "conversation_id": conversation_id,
            "message_id": assistant_message["id"],
            **result,
        }

    def get_conversation(self, conversation_id: str) -> dict:
        with self._lock:
            conversation = self._get_or_create(conversation_id)
            return {
                "conversation_id": conversation.conversation_id,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "message_count": len(conversation.messages),
                "status": conversation.status,
                "handoff_reason": conversation.handoff_reason,
            }

    def get_messages(self, conversation_id: str) -> list[dict]:
        with self._lock:
            conversation = self._get_or_create(conversation_id)
            return list(conversation.messages)


customer_service = CustomerServiceService()
