"""文本清洗与结构感知切块。

切块以 Markdown 标题层级和段落边界为边界。``chunk_size`` 只是聚合目标，
不会把一个段落按固定字数截断；超过目标的段落会作为完整原子段落保留。
"""
from __future__ import annotations

import re
from collections import Counter


_CN_PAGE = re.compile(r"^第\s*[0-9一二三四五六七八九十]+\s*页$")
_PAGE = re.compile(r"^[0-9]+\s*/\s*[0-9]+$")
_ATX_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT = re.compile(r"^\s*(=+|-+)\s*$")


def _clean_lines(raw: str) -> list[str]:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"https?://\S+", "", text)

    lines = [line.strip() for line in text.split("\n")]
    freq = Counter(line for line in lines if line)
    boilerplate = {line for line, count in freq.items() if count >= 3 and len(line) <= 30}
    cleaned: list[str] = []
    for line in lines:
        if not line or line in boilerplate:
            cleaned.append("")
            continue
        if _CN_PAGE.match(line) or _PAGE.match(line):
            cleaned.append("")
            continue
        cleaned.append(line)
    return cleaned


def clean_text(raw: str) -> list[str]:
    """清洗文本并返回非空行，保留历史调用方的返回形状。"""
    return [line for line in _clean_lines(raw) if line]


def _heading(line: str) -> tuple[int, str] | None:
    match = _ATX_HEADING.match(line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _structure_blocks(lines: list[str]) -> list[dict]:
    blocks: list[dict] = []
    paragraph: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        current_heading = _heading(line)
        if current_heading:
            if paragraph:
                blocks.append({"kind": "paragraph", "lines": paragraph})
                paragraph = []
            level, title = current_heading
            blocks.append({"kind": "heading", "level": level, "title": title})
            index += 1
            continue
        if index + 1 < len(lines) and line and _SETEXT.match(lines[index + 1]):
            if paragraph:
                blocks.append({"kind": "paragraph", "lines": paragraph})
                paragraph = []
            level = 1 if lines[index + 1].startswith("=") else 2
            blocks.append({"kind": "heading", "level": level, "title": line})
            index += 2
            continue
        if line:
            paragraph.append(line)
        elif paragraph:
            blocks.append({"kind": "paragraph", "lines": paragraph})
            paragraph = []
        index += 1
    if paragraph:
        blocks.append({"kind": "paragraph", "lines": paragraph})
    return blocks


def parse_document_structure(raw: str) -> list[dict]:
    """解析标题层级和段落，返回带章节路径的段落记录。"""
    stack: list[tuple[int, str]] = []
    paragraphs: list[dict] = []
    for block in _structure_blocks(_clean_lines(raw)):
        if block["kind"] == "heading":
            level = block["level"]
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block["title"]))
            continue
        path = [title for _, title in stack]
        text = "\n".join(block["lines"]).strip()
        if text:
            paragraphs.append({
                "text": text,
                "para_index": len(paragraphs),
                "heading_path": path,
            })
    return paragraphs


def _make_chunk(records: list[dict], seq: int) -> dict:
    path = list(records[0].get("heading_path", []))
    text = "\n\n".join(record["text"] for record in records).strip()
    section = " / ".join(path)
    start = min(record["para_index"] for record in records)
    end = max(record["para_index"] for record in records) + 1
    return {
        "text": text,
        "embedding_text": f"{section}\n{text}" if section else text,
        "seq": seq,
        "para_range": (start, end),
        "heading_path": path,
        "chapter": path[0] if path else "",
        "title": path[-1] if path else "",
        "section": section,
    }


def chunk_sections(sections: list[dict], chunk_size: int = 800, overlap: int = 0) -> list[dict]:
    """按章节路径和段落边界聚合结构化段落。

    ``overlap`` 为兼容旧配置保留；大于零时最多复用一个完整的前置段落，
    不会做字符级 overlap。章节切换时不跨章节复用段落。
    """
    if not sections:
        return []
    target = max(1, chunk_size)
    chunks: list[dict] = []
    current: list[dict] = []

    def emit() -> None:
        nonlocal current
        if current:
            chunks.append(_make_chunk(current, len(chunks)))
            current = []

    for record in sections:
        path = record.get("heading_path", [])
        same_section = current and current[0].get("heading_path", []) == path
        candidate = current + [record] if same_section else [record]
        candidate_size = len("\n\n".join(item["text"] for item in candidate))
        if current and not same_section:
            emit()
        elif current and candidate_size > target:
            previous = current[-1] if overlap and len(current[-1]["text"]) <= target else None
            emit()
            current = [previous] if previous else []
        current.append(record)

    emit()
    return chunks


def chunk_paragraphs(paragraphs: list[str], chunk_size: int = 800, overlap: int = 0) -> list[dict]:
    """兼容旧调用方：把普通段落视为无标题结构后切块。"""
    sections = [
        {"text": paragraph, "para_index": index, "heading_path": []}
        for index, paragraph in enumerate(paragraphs)
        if paragraph and paragraph.strip()
    ]
    return chunk_sections(sections, chunk_size, overlap)


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 0) -> list[dict]:
    """原始文档 → 结构感知 chunk 列表。"""
    return chunk_sections(parse_document_structure(text), chunk_size, overlap)
