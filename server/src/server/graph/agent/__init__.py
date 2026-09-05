"""RAG agent 对外入口：封装图的构建与调用。"""
from __future__ import annotations

from server.graph.graph import build_rag_graph
from server.graph.nodes import Generator, Reranker, Retriever


class RagAgent:
    """RAG 问答 agent，封装 LangGraph 图。"""

    def __init__(
        self,
        retriever: Retriever | None = None,
        reranker: Reranker | None = None,
        generator: Generator | None = None,
    ):
        self.graph = build_rag_graph(retriever, reranker, generator)

    def ask(self, question: str, top_k: int = 5) -> dict:
        """同步问答，返回 {answer, citations, final_answer, contexts}。"""
        result = self.graph.invoke(
            {"question": question, "top_k": top_k, "rewrites": 0}
        )
        return result

    async def aask(self, question: str, top_k: int = 5) -> dict:
        """异步问答。"""
        result = await self.graph.ainvoke(
            {"question": question, "top_k": top_k, "rewrites": 0}
        )
        return result


def create_agent(
    retriever: Retriever | None = None,
    reranker: Reranker | None = None,
    generator: Generator | None = None,
) -> RagAgent:
    """便捷工厂。"""
    return RagAgent(retriever, reranker, generator)
