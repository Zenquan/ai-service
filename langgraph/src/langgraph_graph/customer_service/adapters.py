"""客服编排层到现有 RAG Core 的适配器。"""
from __future__ import annotations

import sys
from pathlib import Path

from langgraph_graph.customer_service.nodes import Generator, Retriever

_RAG_DIR = Path(__file__).resolve().parents[4] / "rag"


def _prepare_rag_import() -> None:
    if str(_RAG_DIR) not in sys.path:
        sys.path.insert(0, str(_RAG_DIR))


def rag_retriever(query: str, top_k: int = 5) -> list[dict]:
    """延迟加载 rag.retrieve，避免客服图测试强制加载 embedding 依赖。"""
    _prepare_rag_import()
    from retrieve import retrieve

    return retrieve(query, top_k=top_k)


def rag_generator(question: str, contexts: list[dict], history: list) -> tuple[str, list[int]]:
    """延迟加载 rag.generate，并统一为客服 Generator 契约。"""
    _prepare_rag_import()
    from generate import generate

    result = generate(question, contexts)
    return result.get("answer", ""), result.get("citations", [])


def default_retriever() -> Retriever:
    return rag_retriever


def default_generator() -> Generator:
    return rag_generator
