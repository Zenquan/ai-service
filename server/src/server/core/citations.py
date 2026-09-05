"""引用校验：纯函数、零外部依赖（可离线单测）。"""
from __future__ import annotations

import re

_REF_PAT = re.compile(r"\[来源(\d+)\]")


def verify_citations(answer: str, material_count: int) -> dict:
    """校验回答中的 [来源N] 引用是否在素材范围内。

    - citations: 全部引用的序号
    - invalid: 越界序号（>material_count 或 <=0）——越界 = 模型编造，防幻觉拦截点
    - valid: 是否全部合法
    """
    citations = [int(n) for n in _REF_PAT.findall(answer)]
    invalid = [n for n in citations if not (1 <= n <= material_count)]
    return {
        "citations": citations,
        "invalid": invalid,
        "valid": not invalid,
    }