"""RAG 图单元测试：验证节点串联、条件边（引用校验→重写）与终态汇总。"""
from __future__ import annotations

import sys
from pathlib import Path

# 兼容「python tests/test_rag_graph.py」直接运行：把 src/ 加入搜索路径
# （src 布局的包未安装时不会自动在 sys.path，pytest 需配合 -c/rootdir 或安装后导入）
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from langgraph_graph.agent import create_agent
from langgraph_graph.graph import build_rag_graph


def _retriever(contexts):
    def search(query: str, top_k: int = 5) -> list[dict]:
        return contexts[:top_k]
    return search


def _generator(answer_with_citations: str):
    def generate(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
        return answer_with_citations, []
    return generate


def test_graph_builds():
    g = build_rag_graph()
    assert g is not None


def test_flow_valid_citations():
    """合法引用：一次通过校验，不需要重写。"""
    agent = create_agent(
        retriever=_retriever([
            {"chunk_id": "c1", "text": "A", "source": "s", "score": 1.0},
            {"chunk_id": "c2", "text": "B", "source": "s", "score": 0.9},
        ]),
        generator=_generator("答案是 A[1]"),
    )
    result = agent.ask("问题", top_k=2)
    assert result["needs_revision"] is False
    assert result["final_answer"].startswith("答案是 A[1]")
    assert "引用" in result["final_answer"]


def test_flow_rewrite_on_hallucination():
    """幻觉引用 [9]：应触发重写，但重试 2 次后仍未通过则输出。"""
    agent = create_agent(
        retriever=_retriever([
            {"chunk_id": "c1", "text": "A", "source": "s", "score": 1.0},
        ]),
        generator=_generator("答案是 A[9]"),  # 引用 9 超出上下文
    )
    result = agent.ask("问题", top_k=1)
    assert result["needs_revision"] is True
    # 最多重写 2 次，最终在超限后落到 finalize
    assert result["rewrites"] == 2
    assert result["final_answer"]
