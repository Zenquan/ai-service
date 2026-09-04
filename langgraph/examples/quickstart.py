"""可独立运行的 RAG 问答示例（注入 mock 检索/生成，无需 rag 核心或真实 LLM）。

用法：
    python examples/quickstart.py "什么是混合检索？"
"""
from __future__ import annotations

import sys

from langgraph_graph.agent import create_agent

# --- 注入的替身（演示用）---
_FAKE_CONTEXT = [
    {"chunk_id": "c1", "text": "混合检索同时使用关键词（BM25）与语义向量，提升召回质量。", "source": "docs/rag.md", "score": 0.92},
    {"chunk_id": "c2", "text": "rerank 阶段对检索结果做二次排序，剔除无关片段。", "source": "docs/rag.md", "score": 0.85},
]


def fake_retriever(query: str, top_k: int = 5) -> list[dict]:
    return _FAKE_CONTEXT[:top_k]


def fake_generator(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
    answer = "混合检索结合了 BM25 关键词与语义向量，能显著提升召回质量[1]。"
    return answer, [1]


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "什么是混合检索？"
    agent = create_agent(
        retriever=fake_retriever,
        generator=fake_generator,
    )
    result = agent.ask(question, top_k=2)
    print("\n=== 最终答案 ===")
    print(result.get("final_answer"))
    print("\n=== 引用 ===", result.get("citations"))
    print("=== 重写次数 ===", result.get("rewrites"))


if __name__ == "__main__":
    main()
