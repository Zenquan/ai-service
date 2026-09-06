"""生成层：检索结果结构化注入 → DeepSeek 生成 → 引用校验闭环。

防幻觉设计（继承 AI 小灵项目的思想）：
1. 注入的每条素材带编号，正文强制用 [来源N] 标记
2. 生成后程序校验 [来源N] 序号是否在注入素材范围内（序号越界 = 模型编造，拦下）

PII 脱敏（迭代「安全与可靠性加固」）：
- 进入 Prompt 之前，``query`` 与 ``messages`` 经 ``server.services.redactor`` 清洗；
- 默认 ``REDACTOR_REDACT_MATERIALS=0``，知识库素材保留原文以保证回答质量；
  合规更严时可置 1 开启。
"""
from __future__ import annotations

import logging
import os

from openai import OpenAI

from server.services.redactor import redact_text

from .citations import verify_citations

from . import config

logger = logging.getLogger(__name__)

# 知识库素材是否脱敏：默认 0（保留原文），合规需要时改 1。
_REDACT_MATERIALS = os.getenv("REDACTOR_REDACT_MATERIALS", "").strip().lower() in {"1", "true", "yes", "on"}

_SYSTEM = (
    "你是一个严格依据提供资料回答的助手。\n"
    "规则：\n"
    "1. 只能使用【资料】中明确存在的事实作答，不得编造或臆测；\n"
    "2. 引用资料必须用 [来源N] 标记，N 为资料编号（从 1 开始）；\n"
    "3. 资料未覆盖的信息，明确说明'资料中未提及'，不要猜测。"
)


def _build_user_prompt(query: str, materials: list[dict]) -> str:
    """素材编号注入：每个 [N] 对应一条 chunk（text + 出处）。

    注意：进入 LLM 之前，``query`` 已经过 ``server.services.redactor`` 脱敏（见下方
    ``generate`` / ``generate_stream``），这里不再重复处理；素材 ``text`` 默认保留
    原文以保证回答准确率，仅当 ``REDACTOR_REDACT_MATERIALS=1`` 时才脱敏。
    """
    blocks = []
    for i, m in enumerate(materials, start=1):
        section = m.get("section") or m.get("title")
        label = f"，章节：{section}" if section else ""
        text = m["text"]
        if _REDACT_MATERIALS:
            text = redact_text(text)
        blocks.append(f"[来源{i}]（来自《{m['doc']}》{label}）\n{text}")
    return (
        f"【资料】\n" + "\n\n".join(blocks) + f"\n\n【问题】\n{query}\n\n请依据资料回答，并标注引用。"
    )


def _build_client() -> OpenAI:
    """构建 DeepSeek OpenAI 兼容客户端（超时 + 重试）。"""
    return OpenAI(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        timeout=60.0,        # P1 健壮性：显式超时（默认 600s 卡死 worker）
        max_retries=2,       # P1 健壮性：瞬时错误重试（连接/5xx/429），openai SDK 内置指数退避
    )


def generate(query: str, materials: list[dict], model: str | None = None) -> dict:
    """生成并校验引用。返回 {answer, citations, valid}。"""
    if not config.LLM_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY（在 .env 里配置）")

    # 进入 LLM 前对 query 做 PII 脱敏，避免敏感字段上传到第三方。
    safe_query = redact_text(query)

    logger.info(
        "LLM 生成开始 model=%s materials=%d query_len=%d",
        model or config.LLM_MODEL,
        len(materials),
        len(safe_query),
    )
    client = _build_client()
    resp = client.chat.completions.create(
        model=model or config.LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_prompt(safe_query, materials)},
        ],
        temperature=0.3,  # 低随机性：RAG 回答要稳定可溯源
    )
    answer = resp.choices[0].message.content or ""

    check = verify_citations(answer, len(materials))
    logger.info(
        "LLM 生成完成 answer_len=%d citations=%s valid=%s",
        len(answer),
        check["citations"],
        check["valid"],
    )
    return {
        "answer": answer,
        "citations": check["citations"],
        "valid": check["valid"],
        "material_count": len(materials),
    }


def generate_stream(query: str, materials: list[dict], model: str | None = None):
    """流式生成（SSE 用）：yield token 增量字符串，结束后 yield 校验结果 dict。

    用法：
        stream = generate_stream(query, materials)
        for chunk in stream:
            if isinstance(chunk, str):
                ... token 增量（"content"）
            else:
                ... {"answer", "citations", "valid", "material_count"}（终态）
    """
    if not config.LLM_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY（在 .env 里配置）")

    # 进入 LLM 前对 query 做 PII 脱敏，避免敏感字段上传到第三方。
    safe_query = redact_text(query)

    logger.info(
        "LLM 流式生成开始 model=%s materials=%d query_len=%d",
        model or config.LLM_MODEL,
        len(materials),
        len(safe_query),
    )
    client = _build_client()
    response = client.chat.completions.create(
        model=model or config.LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_prompt(safe_query, materials)},
        ],
        temperature=0.3,
        stream=True,
    )

    parts: list[str] = []
    for chunk in response:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            parts.append(delta)
            yield delta

    answer = "".join(parts)
    check = verify_citations(answer, len(materials))
    logger.info(
        "LLM 流式生成完成 answer_len=%d citations=%s valid=%s",
        len(answer),
        check["citations"],
        check["valid"],
    )
    yield {
        "answer": answer,
        "citations": check["citations"],
        "valid": check["valid"],
        "material_count": len(materials),
    }
