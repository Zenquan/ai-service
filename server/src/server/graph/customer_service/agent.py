"""客服 Agent 对外入口。"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver

from server.graph.customer_service.adapters import default_generator, default_retriever
from server.graph.customer_service.classifier import Classifier
from server.graph.customer_service.graph import build_customer_service_graph
from server.graph.customer_service.nodes import Generator, Retriever


class CustomerServiceAgent:
    def __init__(
        self,
        retriever: Retriever | None = None,
        generator: Generator | None = None,
        classifier: Classifier | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        circuit_breaker=None,
    ):
        self.checkpointer = checkpointer
        self.circuit_breaker = circuit_breaker
        self.graph = build_customer_service_graph(
            retriever or default_retriever(),
            generator or default_generator(),
            classifier,
            circuit_breaker=circuit_breaker,
            checkpointer=checkpointer,
        )

    def _config(self, conversation_id: str) -> dict:
        return {"configurable": {"thread_id": conversation_id}}

    def _payload(
        self,
        query: str,
        conversation_id: str,
        user_id: str | None,
        tenant_id: str | None,
    ) -> dict:
        # 只注入本轮用户消息；历史由 checkpointer 的 messages 通道（add_messages）跨轮累加。
        return {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "current_query": query,
            "messages": [{"role": "user", "content": query}],
        }

    def ask(
        self,
        query: str,
        conversation_id: str = "anonymous",
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        return self.graph.invoke(
            self._payload(query, conversation_id, user_id, tenant_id),
            self._config(conversation_id),
        )

    async def aask(
        self,
        query: str,
        conversation_id: str = "anonymous",
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        return await self.graph.ainvoke(
            self._payload(query, conversation_id, user_id, tenant_id),
            self._config(conversation_id),
        )

    def get_state(self, conversation_id: str) -> dict | None:
        """返回最近 checkpoint 的图状态值；无 checkpointer 或无记录时返回 None。"""
        if self.checkpointer is None:
            return None
        snapshot = self.graph.get_state(self._config(conversation_id))
        values = dict(snapshot.values or {}) if snapshot else {}
        return values or None


def create_customer_service_agent(
    retriever: Retriever | None = None,
    generator: Generator | None = None,
    classifier: Classifier | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    circuit_breaker=None,
) -> CustomerServiceAgent:
    return CustomerServiceAgent(retriever, generator, classifier, checkpointer, circuit_breaker)
