"""检索节点：基于用户问题检索相关文档片段。

复用 rag 核心（FastEmbed 向量 + Qdrant 混合检索）。
为保持图可独立测试，检索器通过依赖注入传入；
若未注入且可导入 rag 核心，则自动加载默认检索器。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol

from langgraph_graph.state import RAGState

if TYPE_CHECKING:
    from rag.retrieve import retrieve as _rag_retrieve  # type: ignore[import-not-found]


# 检索器：callable(query, top_k) -> list[dict]，与 rag.retrieve.retrieve 同形
Retriever = Callable[[str, int], list[dict]]


class RetrieverProto(Protocol):
    def __call__(self, query: str, top_k: int = 5) -> list[dict]: ...


def _default_retriever() -> RetrieverProto | None:
    """尝试加载 rag 核心的默认检索器（rag.retrieve.retrieve）。"""
    try:
        from rag.retrieve import retrieve as rag_retrieve  # type: ignore[import-not-found]

        return rag_retrieve  # type: ignore[return-value]
    except Exception:
        return None


def retrieve(state: RAGState, retriever: Retriever | None = None) -> RAGState:
    """根据 question 检索 top_k 个上下文片段，写入 contexts。"""
    question = state.get("question", "")
    top_k = state.get("top_k", 5)

    r = retriever or _default_retriever()
    if r is None:
        # 未接入 rag 核心时，返回空上下文，后续生成节点会降级提示
        contexts: list[dict] = []
    else:
        results = r(question, top_k=top_k)
        contexts = [dict(res) for res in results]

    return RAGState(
        question=question,
        top_k=top_k,
        query=question,
        contexts=contexts,
    )
