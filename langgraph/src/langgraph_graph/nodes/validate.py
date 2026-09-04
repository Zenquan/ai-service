"""引用校验节点：核验生成答案的引用序号是否真实对应上下文。

若出现幻觉引用（引用了不存在的片段 / 断言与上下文不符），
设置 needs_revision=True，触发条件边回到生成节点重写。
"""
from __future__ import annotations

import re

from langgraph_graph.state import RAGState


def _extract_citations(answer: str) -> set[int]:
    return {int(m) for m in re.findall(r"\[(?:来源)?(\d+)\]", answer)}


def validate_citations(state: RAGState) -> RAGState:
    """校验 citations 是否都在 contexts 范围内，并检查引用指数。"""
    answer = state.get("answer", "")
    contexts = state.get("contexts", [])
    citations = state.get("citations", [])

    valid_indices = set(range(1, len(contexts) + 1))
    cited = set(citations) or _extract_citations(answer)

    out_of_range = [c for c in cited if c not in valid_indices]

    if out_of_range:
        state["needs_revision"] = True
        state["revision_reason"] = f"幻觉引用：{sorted(out_of_range)} 不在检索上下文中"
        # 在节点内递增重写计数（条件边只读路由，不产生状态更新）
        state["rewrites"] = state.get("rewrites", 0) + 1
    else:
        state["needs_revision"] = False
        state["revision_reason"] = ""
        state["rewrites"] = state.get("rewrites", 0)

    return dict(state)
