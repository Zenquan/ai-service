"""RAG 问答 LangGraph 图。

流程：retrieve → rerank → generate → validate_citations
条件边：validate 通过 → finalize；未通过且未超过重试上限 → generate（重写）。

各节点默认使用 rag 核心实现，可通过 build_rag_graph 注入替身以便单元测试。
"""
from __future__ import annotations

from typing import Callable

from langgraph.graph import END, START, StateGraph

from server.graph.nodes import (
    Generator,
    Reranker,
    Retriever,
    generate,
    rerank_nodes,
    retrieve,
    validate_citations,
)
from server.graph.state import RAGState

MAX_REWRITES = 2


def _should_rewrite(state: RAGState) -> str:
    """条件边：校验失败且重写次数未达上限则回到生成节点（只读路由，不修改状态）。"""
    if state.get("needs_revision") and state.get("rewrites", 0) < MAX_REWRITES:
        return "generate"
    return "finalize"


def finalize(state: RAGState) -> RAGState:
    """终态节点：把 answer + citations 汇总到 final_answer。"""
    import re

    answer = state.get("answer", "")
    citations = state.get("citations", [])
    if not citations:
        # 生成器未显式返回引用时，从 answer 的 [n] 标记兜底提取
        citations = sorted({int(m) for m in re.findall(r"\[(\d+)\]", answer)})
    out = dict(state)
    if citations:
        out["final_answer"] = f"{answer}\n\n引用：{citations}"
    else:
        out["final_answer"] = answer
    return out


def build_rag_graph(
    retriever: Retriever | None = None,
    reranker: Reranker | None = None,
    generator: Generator | None = None,
):
    """构建并编译 RAG 问答图。

    依赖注入入口：不传则各节点尝试加载 rag 核心默认实现；
    单元测试可注入 mock retriever/generator。
    返回编译后的 StateGraph。
    """
    from server.graph.state import RAGState as _RS

    def make_retrieve(state: _RS) -> _RS:
        return retrieve(state, retriever)

    def make_rerank(state: _RS) -> _RS:
        return rerank_nodes(state, reranker)

    def make_generate(state: _RS) -> _RS:
        return generate(state, generator)

    g = StateGraph(RAGState)
    g.add_node("retrieve", make_retrieve)
    g.add_node("rerank", make_rerank)
    g.add_node("generate", make_generate)
    g.add_node("validate", validate_citations)
    g.add_node("finalize", finalize)

    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "rerank")
    g.add_edge("rerank", "generate")
    g.add_edge("generate", "validate")
    g.add_conditional_edges(
        "validate",
        _should_rewrite,
        {"generate": "generate", "finalize": "finalize"},
    )
    g.add_edge("finalize", END)

    return g.compile()
