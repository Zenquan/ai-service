"""客服编排层到 RAG Core 的适配器。

延迟导入 server.core：客服图测试注入 mock 时不需要加载 embedding 依赖。
"""
from __future__ import annotations

from server.graph.customer_service.nodes import Generator, Retriever


def rag_retriever(query: str, top_k: int = 5) -> list[dict]:
    """延迟加载 server.core.retrieve，避免客服图测试强制加载 embedding 依赖。"""
    from server.core.retrieve import retrieve

    return retrieve(query, top_k=top_k)


def rag_generator(question: str, contexts: list[dict], history: list) -> tuple[str, list[int]]:
    """延迟加载 server.core.generate，并统一为客服 Generator 契约。"""
    from server.core.generate import generate

    result = generate(question, contexts)
    return result.get("answer", ""), result.get("citations", [])


def default_retriever() -> Retriever:
    return rag_retriever


def default_generator() -> Generator:
    return rag_generator
