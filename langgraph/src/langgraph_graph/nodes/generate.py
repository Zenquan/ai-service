"""生成节点：基于检索上下文生成带引用的答案。

LLM 通过注入的 callable 提供（可测），默认实现走 OpenAI 兼容接口（DeepSeek）。
"""
from __future__ import annotations

import os
from typing import Callable, Protocol

from langgraph_graph.state import RAGState

# 生成器签名：given(question, contexts, history) -> (answer, citations)
Generator = Callable[[str, list[dict], list], tuple[str, list]]


def _build_prompt(question: str, contexts: list[dict]) -> str:
    ctx_text = "\n\n".join(
        f"[{i + 1}] {c.get('text', c.get('content', ''))}" for i, c in enumerate(contexts)
    )
    return (
        "你是严谨的 RAG 助手。仅基于以下上下文回答用户问题，若上下文不足请明确说明。\n"
        "回答末尾用 [1][2] 形式标注引用，对应上下文序号。\n\n"
        f"=== 上下文 ===\n{ctx_text}\n\n=== 问题 ===\n{question}\n"
    )


def _default_generator() -> Generator:
    """优先复用 rag 核心生成器；否则退回 OpenAI 兼容客户端（DeepSeek）。"""
    try:
        from rag import generate as rag_generate  # type: ignore[import-not-found]

        def via_rag(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
            # rag.generate.generate(query, materials, model) -> {answer, citations, valid}
            out = rag_generate.generate(question, contexts)
            return out.get("answer", ""), out.get("citations", [])

        return via_rag
    except Exception:
        pass

    # 兜底：OpenAI 兼容客户端（DeepSeek），用环境变量配置
    from openai import OpenAI  # type: ignore[import-not-found]

    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY", "sk-no-key"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    def via_openai(question: str, contexts: list[dict], history: list) -> tuple[str, list]:
        messages = [{"role": "user", "content": _build_prompt(question, contexts)}]
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0.2)
        answer = resp.choices[0].message.content or ""
        # 简单从 [n] 提取引用序号
        import re

        citations = [int(m) for m in re.findall(r"\[(\d+)\]", answer)]
        return answer, citations

    return via_openai


def generate(state: RAGState, generator: Generator | None = None) -> RAGState:
    """根据 question + contexts 生成答案，解析引用，写入 answer/citations。"""
    question = state.get("question", "")
    contexts = state.get("contexts", [])
    history = state.get("messages", [])

    gen = generator or _default_generator()
    answer, citations = gen(question, contexts, history)

    out = dict(state)
    out["answer"] = answer
    out["citations"] = citations
    return out
