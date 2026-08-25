"""清洗 + 切块（chunker 独立成模块，纯函数便于单测）。

清洗：统一换行 → 去 HTML/URL/页码行 → 高频短行（页眉页脚/水印）→ 去空行。
切块：段落边界优先聚合 + 超长段强制切，带 overlap 保留上下文。
"""
from __future__ import annotations

import re
from collections import Counter


def clean_text(raw: str) -> list[str]:
    """清洗 → 干净段落列表（链接着 chunker）。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)          # 保留 \n\n 段落边界
    text = re.sub(r"<[^>]+>", "", text)             # 去 HTML 标签
    text = re.sub(r"https?://\S+", "", text)        # 去 URL

    lines = [ln.strip() for ln in text.split("\n")]
    # 页码/页眉：短行 + 高频重复（出现≥3次且≤30字符 = 固定版式）
    freq = Counter(lines)
    boilerplate = {ln for ln, n in freq.items() if n >= 3 and len(ln) <= 30}
    lines = [ln for ln in lines if ln and ln not in boilerplate]

    # 去"第X页 / X/Y"页码行
    page_pat = re.compile(r"^(第\s*[0-9一二三四五六七八九十]+\s*页|[0-9]+\s*/\s*[0-9]+)$")
    return [ln for ln in lines if not page_pat.match(ln)]


def chunk_paragraphs(paragraphs: list[str], chunk_size: int = 800, overlap: int = 100) -> list[dict]:
    """段落列表 → chunk 列表（带原文位置元数据）。

    返回 [{"text": str, "seq": int}]，seq 用于恢复文档顺序/定位原文。
    """
    chunks: list[dict] = []
    current = ""
    cur_start = 0

    for idx, para in enumerate(paragraphs):
        # 超长段落强制切
        while len(para) > chunk_size:
            if current:
                chunks.append({"text": current, "seq": len(chunks), "para_range": (cur_start, idx)})
                current = ""
            chunks.append({"text": para[:chunk_size], "seq": len(chunks), "para_range": (idx, idx + 1)})
            para = para[chunk_size - overlap:] if overlap > 0 else para[chunk_size:]

        if len(current) + len(para) + 1 > chunk_size:
            if current:
                chunks.append({"text": current, "seq": len(chunks), "para_range": (cur_start, idx)})
            current = para
            cur_start = idx
        else:
            current = f"{current}\n{para}" if current else para

    if current:
        chunks.append({"text": current, "seq": len(chunks), "para_range": (cur_start, len(paragraphs))})

    return chunks


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[dict]:
    """入口：原始文本 → chunk 列表。"""
    paragraphs = clean_text(text)
    if not paragraphs:
        return []
    return chunk_paragraphs(paragraphs, chunk_size, overlap)