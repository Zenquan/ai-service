"""纯函数单测：清洗 / 切块 / 引用校验 / 混合检索（不依赖外部服务，可离线跑）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from chunker import chunk_text, clean_text  # noqa: E402
from citations import verify_citations  # noqa: E402
from retrieve import _tokenize, _rrf_merge  # noqa: E402


class TestClean:
    def test_strip_and_drop_empty(self):
        paras = clean_text("  a  \n\n\n  b  \n  ")
        assert paras == ["a", "b"]

    def test_boilerplate_header_footer_removed(self):
        text = "公司内部资料\n正文第一段\n公司内部资料\n正文第二段\n公司内部资料"
        paras = clean_text(text)
        assert "公司内部资料" not in paras
        assert len(paras) == 2

    def test_page_number_removed(self):
        paras = clean_text("第1页\n内容\n2 / 5\n更多内容")
        assert paras == ["内容", "更多内容"]

    def test_html_and_url_stripped(self):
        paras = clean_text("<p>正文</p> 看 https://example.com/x 这里")
        assert all("<" not in p and "http" not in p for p in paras)


class TestChunk:
    def test_short_text_single_chunk(self):
        chunks = chunk_text("只有一段话")
        assert len(chunks) == 1
        assert chunks[0]["seq"] == 0

    def test_long_text_multiple_chunks_with_overlap(self):
        para = "午" * 1000  # 单段超长 → 强制切
        chunks = chunk_text(para, chunk_size=300, overlap=50)
        assert len(chunks) >= 3
        # overlap：相邻块尾部/头部应有重复内容
        assert chunks[0]["text"][-50:] in chunks[1]["text"]

    def test_paragraph_boundary_preferred(self):
        text = "\n".join([f"段落{i}" + "字" * 200 for i in range(6)])
        chunks = chunk_text(text, chunk_size=500, overlap=0)
        assert all("段落" in c["text"] for c in chunks)

    def test_empty_returns_empty(self):
        assert chunk_text("   \n\n  ") == []


class TestCitations:
    def test_valid_citations(self):
        r = verify_citations("答案[来源1]和[来源2]", material_count=3)
        assert r["valid"] and r["invalid"] == []

    def test_out_of_range_detected(self):
        r = verify_citations("编造[来源9]", material_count=3)
        assert not r["valid"] and r["invalid"] == [9]

    def test_no_citation_still_valid_but_empty(self):
        r = verify_citations("没有引用", material_count=3)
        assert r["citations"] == []


class TestMixedRetrieval:
    """混合检索纯函数：tokenize（中文 2-gram + 英文单词）与 RRF 融合。"""

    def test_tokenize_chinese_bigram_and_english(self):
        t = _tokenize("AI Agent 多智能体协作")
        assert "ai" in t and "agent" in t
        assert "多智" in t or "智能" in t

    def test_rrf_merges_union_keeps_unique(self):
        vec = [{"doc": "a", "seq": 1, "text": "x"}, {"doc": "b", "seq": 2, "text": "y"}]
        kw = [{"doc": "b", "seq": 2, "text": "y"}, {"doc": "c", "seq": 3, "text": "z"}]
        merged = _rrf_merge(vec, kw)
        # 三份文档都在，无重复
        assert len(merged) == 3
        keys = {(i["doc"], i["seq"]) for i in merged}
        assert keys == {("a", 1), ("b", 2), ("c", 3)}

    def test_rrf_ranks_doc_in_both_lists_first(self):
        vec = [{"doc": "a", "seq": 1, "text": "x"}]
        kw = [{"doc": "a", "seq": 1, "text": "x"}, {"doc": "b", "seq": 2, "text": "y"}]
        merged = _rrf_merge(vec, kw)
        # 双路都命中的 (a,1) 排最前（RRF 融合的核心收益）
        assert (merged[0]["doc"], merged[0]["seq"]) == ("a", 1)