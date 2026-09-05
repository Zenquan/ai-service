"""LangGraph 状态类型定义。

RAG 问答流程的状态，供各节点读取/写入。
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


def _merge_text(a: str | None, b: str) -> str:
    """合并可变文本字段（后续节点覆盖前序节点）。"""
    return b if b else (a or "")


def _merge_citations(a: list | None, b: list) -> list:
    """合并引用列表，默认去重保留顺序。"""
    seen: set = set()
    out: list = []
    for item in (a or []) + b:
        key = str(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


class RAGState(TypedDict, total=False):
    """问答流程的完整状态。"""

    # 输入
    question: str
    top_k: int  # 检索条数
    rewrites: int = 0  # 已重写次数（引用校验失败后回到 generate）

    # 检索阶段
    contexts: list[dict]  # [{chunk_id, text, source, score}]
    query: str  # 归一化后的检索 query

    # 生成阶段
    answer: str
    citations: Annotated[list, _merge_citations]  # 引用
    # LangChain 消息流（对话记忆 / 流式输出）
    messages: Annotated[list, add_messages]

    # 校验阶段
    needs_revision: bool  # 引用校验是否要求重写
    revision_reason: str

    # 终态
    final_answer: str
