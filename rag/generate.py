"""生成层：检索结果结构化注入 → DeepSeek 生成 → 引用校验闭环。

防幻觉设计（继承 AI 小灵项目的思想）：
1. 注入的每条素材带编号，正文强制用 [来源N] 标记
2. 生成后程序校验 [来源N] 序号是否在注入素材范围内（序号越界 = 模型编造，拦下）
"""
from __future__ import annotations

from openai import OpenAI

from citations import verify_citations

import config

_SYSTEM = (
    "你是一个严格依据提供资料回答的助手。\n"
    "规则：\n"
    "1. 只能使用【资料】中明确存在的事实作答，不得编造或臆测；\n"
    "2. 引用资料必须用 [来源N] 标记，N 为资料编号（从 1 开始）；\n"
    "3. 资料未覆盖的信息，明确说明'资料中未提及'，不要猜测。"
)


def _build_user_prompt(query: str, materials: list[dict]) -> str:
    """素材编号注入：每个 [N] 对应一条 chunk（text + 出处）。"""
    blocks = []
    for i, m in enumerate(materials, start=1):
        section = m.get("section") or m.get("title")
        label = f"，章节：{section}" if section else ""
        blocks.append(f"[来源{i}]（来自《{m['doc']}》{label}）\n{m['text']}")
    return (
        f"【资料】\n" + "\n\n".join(blocks) + f"\n\n【问题】\n{query}\n\n请依据资料回答，并标注引用。"
    )


def generate(query: str, materials: list[dict], model: str | None = None) -> dict:
    """生成并校验引用。返回 {answer, citations, valid}。"""
    if not config.LLM_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY（在 .env 里配置）")

    client = OpenAI(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        timeout=60.0,        # P1 健壮性：显式超时（默认 600s 卡死 worker）
        max_retries=2,       # P1 健壮性：瞬时错误重试（连接/5xx/429），openai SDK 内置指数退避
    )
    resp = client.chat.completions.create(
        model=model or config.LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_prompt(query, materials)},
        ],
        temperature=0.3,  # 低随机性：RAG 回答要稳定可溯源
    )
    answer = resp.choices[0].message.content or ""

    check = verify_citations(answer, len(materials))
    return {
        "answer": answer,
        "citations": check["citations"],
        "valid": check["valid"],
        "material_count": len(materials),
    }
