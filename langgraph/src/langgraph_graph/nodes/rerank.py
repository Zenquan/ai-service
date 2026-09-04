"""Rerank 节点：对检索结果重新排序，去掉无关片段。

复用 rag 核心的 rerank（DeepSeek cross-encoder 或内置模型）。
未注入 reranker 时，直接按原检索 score 排序，不影响流程。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol

from langgraph_graph.state import RAGState

if TYPE_CHECKING:
    from rag import rerank  # type: ignore[import-not-found]

# rerank(query, docs) -> list[dict]，返回重排后的上下文
Reranker = Callable[[str, list[dict]], list[dict]]


class _DefaultRerank(Protocol):
    def __call__(self, query: str, docs: list[dict]) -> list[dict]: ...


def _default_reranker() -> Reranker | None:
    """默认无独立 rerank 步骤。

    rag.retrieve 在检索阶段已内置可选 rerank（SiliconFlow），
    因此这里默认原样透传；如需独立二次精排，请显式注入 reranker。
    """
    return None


def rerank_nodes(state: RAGState, reranker: Reranker | None = None) -> RAGState:
    """对 contexts 进行重排，仅保留相关度达标的片段。"""
    question = state.get("question", "")
    contexts = state.get("contexts", [])

    if not contexts:
        return RAGState(**state, contexts=[])

    r = reranker or _default_reranker()
    if r is not None:
        contexts = r(question, contexts)

    out = dict(state)
    out["contexts"] = contexts
    return out
