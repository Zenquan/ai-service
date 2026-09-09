"""幻觉引用的处理契约：先重写纠正，重写仍不合规才转人工。

回归背景：客服图旧实现里 ``validate_answer`` 一发现引用编号越界就置
``needs_human=True``，把「模型可自纠的小错」当成「必须人工」——和 RAG 主图
（重写 2 次后才放弃）不一致，导致大量正常问答被误判成人工工单。
"""
from __future__ import annotations

from server.graph.customer_service.graph import build_customer_service_graph


def _contexts(query: str, top_k: int = 5) -> list[dict]:
    return [{"text": "AI Agent 介绍", "doc": "store.md", "seq": 0}]


def _sequenced_generator(*answers: str):
    """按调用次序返回不同回答，用于模拟「重写后被纠正」。"""
    calls = {"n": 0}

    def generate(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
        idx = min(calls["n"], len(answers) - 1)
        calls["n"] += 1
        return answers[idx], []

    generate.calls = calls  # type: ignore[attr-defined]
    return generate


def _build(generator):
    return build_customer_service_graph(retriever=_contexts, generator=generator)


def _ask(graph, query: str = "什么是 AI Agent？", thread_id: str = "c-1"):
    return graph.invoke(
        {
            "conversation_id": thread_id,
            "user_id": "demo-user",
            "tenant_id": None,
            "current_query": query,
            "messages": [{"role": "user", "content": query}],
        },
        {"configurable": {"thread_id": thread_id}},
    )


def test_hallucinated_citation_is_rewritten_not_handed_off():
    """引用越界先重写；重写后合规则正常作答，不转人工。"""
    generator = _sequenced_generator("答案是 A[9]", "答案是 A[1]")
    graph = _build(generator)

    result = _ask(graph)

    assert generator.calls["n"] == 2, "应触发一次重写"
    assert result["citation_valid"] is True
    assert result["response_mode"] == "answer"
    assert result["needs_human"] is False
    assert result["handoff_reason"] is None


def test_persistent_hallucination_escalates_to_human():
    """重写后仍然引用越界：才升级人工（避免无限重写）。"""
    generator = _sequenced_generator("答案是 A[9]")
    graph = _build(generator)

    result = _ask(graph)

    assert generator.calls["n"] == 2, "重写次数应有上限，不能无限循环"
    assert result["response_mode"] == "handoff"
    assert result["needs_human"] is True
    assert "引用" in (result["handoff_reason"] or "")


def test_valid_citation_never_triggers_rewrite():
    """引用本来就合规：零重写，直接作答。"""
    generator = _sequenced_generator("答案是 A[1]")
    graph = _build(generator)

    result = _ask(graph)

    assert generator.calls["n"] == 1
    assert result["response_mode"] == "answer"
    assert result["needs_human"] is False
