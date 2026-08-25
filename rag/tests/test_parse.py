"""parse_document 测试：三级解析降级链 + sha256 缓存（打桩云/本地解析器，不调外部）。

关键设计验证：
- txt/md 直通（不查缓存不调云）
- 云解析失败 → markitdown → 二进制兜底
- 缓存命中：同内容重复解析不再次调用解析器
- 缓存 key 是内容 hash（改名/复制仍命中，内容变化则失效）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
import ingest  # noqa: E402


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    """把解析缓存目录指到临时目录，避免污染真实 data/_parse_cache。"""
    monkeypatch.setattr(config, "PARSE_CACHE_DIR", tmp_path / "cache")
    return tmp_path


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return path


class TestTxtDirect:
    def test_txt_passthrough_no_cloud(self, tmp_config, monkeypatch):
        """txt 直通：不应触发任何解析器（计数器不变）。"""
        f = _write(tmp_config / "a.txt", "纯文本内容")
        calls = {"n": 0}

        def boom(*_a, **_k):
            calls["n"] += 1
            raise RuntimeError("不应被调用")

        monkeypatch.setattr(ingest, "_parse_mineru", boom)
        monkeypatch.setattr(ingest, "_parse_markitdown", boom)

        text = ingest.parse_document(f)
        assert text == "纯文本内容"
        assert calls["n"] == 0


class TestFallbackChain:
    def test_cloud_fails_markitdown_succeeds(self, tmp_config, monkeypatch):
        """MinerU 失败 → markitdown 成功：返回 markitdown 结果。"""
        f = _write(tmp_config / "paper.pdf", "pdf bytes 占位")

        def cloud_down(_p):
            raise RuntimeError("cloud down")

        monkeypatch.setattr(ingest, "_parse_mineru", cloud_down)
        monkeypatch.setattr(ingest, "_parse_markitdown", lambda _p: "markitdown 结果")

        text = ingest.parse_document(f)
        assert "markitdown 结果" in text

    def test_all_fail_binary_fallback(self, tmp_config, monkeypatch):
        """MinerU 与 markitdown 都失败 → 二进制强解码兜底。"""
        f = _write(tmp_config / "weird.bin.pdf", "raw-binary-文本")

        def boom(_p):
            raise RuntimeError("no sdk")

        monkeypatch.setattr(ingest, "_parse_mineru", boom)
        monkeypatch.setattr(ingest, "_parse_markitdown", boom)

        text = ingest.parse_document(f)
        assert "raw-binary-文本" in text


class TestParseCache:
    def test_second_parse_hits_cache(self, tmp_config, monkeypatch):
        """同内容二次解析：走缓存，不再次调用解析器。"""
        f = _write(tmp_config / "doc.pdf", "稳定内容 abc")
        calls = {"n": 0}

        def fake_mineru(_p):
            calls["n"] += 1
            return "云解析结果"

        monkeypatch.setattr(ingest, "_parse_mineru", fake_mineru)
        monkeypatch.setattr(ingest, "_parse_markitdown", lambda _p: (_ for _ in ()).throw(RuntimeError("unused")))

        t1 = ingest.parse_document(f)
        assert t1 == "云解析结果"
        t2 = ingest.parse_document(f)
        assert t2 == "云解析结果"
        assert calls["n"] == 1  # 云只被调一次，第二次读缓存

    def test_cache_keyed_by_content_not_path(self, tmp_config, monkeypatch):
        """缓存按内容 hash：复制/改名同内容 → 仍命中缓存。"""
        src = _write(tmp_config / "src.pdf", "同一份内容")
        monkeypatch.setattr(ingest, "_parse_mineru", lambda _p: "解析文本")
        monkeypatch.setattr(ingest, "_parse_markitdown", lambda _p: (_ for _ in ()).throw(RuntimeError("unused")))

        assert ingest.parse_document(src) == "解析文本"

        # 复制到新路径（新文件名），若按内容缓存则应命中，不再调云
        dst = _write(tmp_config / "copy.pdf", "同一份内容")
        monkeypatch.setattr(ingest, "_parse_mineru", lambda _p: "不应被调用")
        assert ingest.parse_document(dst) == "解析文本"

    def test_content_change_invalidates_cache(self, tmp_config, monkeypatch):
        """内容变化 → 新 hash → 重新解析。"""
        f = _write(tmp_config / "doc.pdf", "版本一")
        monkeypatch.setattr(ingest, "_parse_mineru", lambda p: f"解析:{p.read_bytes()[:8]!r}")
        monkeypatch.setattr(ingest, "_parse_markitdown", lambda _p: (_ for _ in ()).throw(RuntimeError("unused")))

        t1 = ingest.parse_document(f)
        f.write_bytes("版本二".encode("utf-8"))
        t2 = ingest.parse_document(f)
        assert t1 != t2


class TestContentHash:
    def test_hash_is_sha256_hex(self, tmp_config):
        f = _write(tmp_config / "x.txt", "hello")
        h = ingest._content_hash(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)