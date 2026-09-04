"""客服 Agent 对外入口。"""
from __future__ import annotations

from langgraph_graph.customer_service.adapters import default_generator, default_retriever
from langgraph_graph.customer_service.classifier import Classifier
from langgraph_graph.customer_service.graph import build_customer_service_graph
from langgraph_graph.customer_service.nodes import Generator, Retriever


class CustomerServiceAgent:
    def __init__(
        self,
        retriever: Retriever | None = None,
        generator: Generator | None = None,
        classifier: Classifier | None = None,
    ):
        self.graph = build_customer_service_graph(
            retriever or default_retriever(),
            generator or default_generator(),
            classifier,
        )

    def ask(
        self,
        query: str,
        conversation_id: str = "anonymous",
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        return self.graph.invoke({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "current_query": query,
            "messages": [{"role": "user", "content": query}],
        })

    async def aask(
        self,
        query: str,
        conversation_id: str = "anonymous",
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        return await self.graph.ainvoke({
            "conversation_id": conversation_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "current_query": query,
            "messages": [{"role": "user", "content": query}],
        })


def create_customer_service_agent(
    retriever: Retriever | None = None,
    generator: Generator | None = None,
    classifier: Classifier | None = None,
) -> CustomerServiceAgent:
    return CustomerServiceAgent(retriever, generator, classifier)
