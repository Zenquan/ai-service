"""文档解析：MinerU 云 SDK（mineru-open-sdk）→ markitdown（轻量）→ 纯文本兜底 + 解析缓存。

设计：解析能力可插拔 + 云解析结果落盘缓存（P0）。
- MinerU 云 SDK（from mineru import MinerU，由 mineru-open-sdk 提供）：
    * Flash 模式（免 token）：client.flash_extract(路径或URL) → MinerU 官方云解析，快速试用
    * 标准模式（需 token，https://mineru.net 免费申请）：client.extract(路径或URL) → 更高精度
- MarkItDown：PDF/Word/HTML 轻量本地解析（无 MinerU/云失败时兜底）
- 纯文本：.txt/.md 直通兜底

解析缓存：以文件内容 sha256 为 key，解析结果落到 data/_parse_cache/<hash>.txt。
同一文件重复 ingest（含换 chunk_size 重跑实验）不再调 MinerU 云，省 API 费 + 快。
每次解析打印实际路径（cache命中/mineru/markitdown/兜底）+ 结果抽样。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import config


def parse_document(path: Path) -> str:
    """解析文档为文本（带缓存）。返回文本；关键信息为缓存命中情况。"""
    ext = path.suffix.lower()

    # 纯文本类（.txt/.md）无需解析也无云调用，直通（也可缓存，但没必要）
    if ext in {".txt", ".md", ".markdown"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        _log(path, "txt直通", text)
        return text

    # 缓存：内容 hash 命中则直接读缓存，不调云
    cache_key = _content_hash(path)
    cached = _read_cache(cache_key)
    if cached is not None:
        _log(path, "cache命中", cached)
        return cached

    # 尝试 MinerU 云 SDK
    try:
        text = _parse_mineru(path)
        if text and text.strip():
            _write_cache(cache_key, text)
            _log(path, "mineru", text)
            return text
    except Exception as e:  # noqa: BLE001 —— 未安装/网络失败/解析失败都降级
        _log(path, "mineru失败", text="", detail=str(e))

    # 尝试 MarkItDown（轻量本地）
    try:
        text = _parse_markitdown(path)
        if text and text.strip():
            _write_cache(cache_key, text)
            _log(path, "markitdown", text)
            return text
    except Exception as e:  # noqa: BLE001
        _log(path, "markitdown失败", text="", detail=str(e))

    # 最后兜底：原始字节强解码（通常乱码，也缓存避免重复）
    text = path.read_bytes().decode("utf-8", errors="replace")
    _write_cache(cache_key, text)
    _log(path, "二进制兜底", text)
    return text


# ── 缓存 ─────────────────────────────────────────────────

def _content_hash(path: Path) -> str:
    """文件内容 sha256（按内容而非路径/时间：改名/复制不影响缓存命中）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _cache_file(cache_key: str) -> Path:
    config.PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.PARSE_CACHE_DIR / f"{cache_key}.txt"


def _read_cache(cache_key: str) -> str | None:
    f = _cache_file(cache_key)
    if f.exists():
        try:
            return f.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return None
    return None


def _write_cache(cache_key: str, text: str) -> None:
    try:
        _cache_file(cache_key).write_text(text, encoding="utf-8")
    except Exception:  # noqa: BLE001 —— 缓存写失败不影响主流程
        pass


# ── 解析器 ───────────────────────────────────────────────

def _log(path: Path, parser: str, text: str, detail: str = "") -> None:
    """打印解析路径 + 结果抽样（前 80 字），方便判断是否为乱码。"""
    sample = (text or "").strip().replace("\n", " ")[:80]
    extra = f" | 失败: {detail}" if detail else ""
    print(f"  [parse] {path.name} → {parser}{extra}")
    if sample:
        print(f"          抽样: {sample}")


def _parse_mineru(path: Path) -> str:
    """MinerU 云 SDK：有 token 用 extract（标准），无 token 用 flash_extract（Flash 免费）。

    返回 Markdown 文本；SDK 不可用/云失败抛异常由上层降级。
    """
    from mineru import MinerU

    client = MinerU(config.MINERU_TOKEN) if config.MINERU_TOKEN else MinerU()
    result = client.extract(str(path)) if config.MINERU_TOKEN else client.flash_extract(str(path))

    md = getattr(result, "markdown", None) or ""
    return str(md)


def _parse_markitdown(path: Path) -> str:
    """MarkItDown：轻量转 Markdown（不依赖 torch）。"""
    from markitdown import MarkItDown  # type: ignore

    md = MarkItDown()
    return str(md.convert(str(path)).text_content or "")