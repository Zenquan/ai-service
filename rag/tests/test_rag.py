"""纯函数单测：清洗 / 切块 / 引用校验 / 混合检索（不依赖外部服务，可离线跑）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from chunker import chunk_text, clean_text  # noqa: E402
from citations import verify_citations  # noqa: E402
import retrieve as retrieve_module  # noqa: E402
from retrieve import _rerank_siliconflow, _rrf_merge, _tokenize  # noqa: E402


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

    def test_long_paragraph_is_not_hard_split(self):
        para = "午" * 1000
        chunks = chunk_text(para, chunk_size=300, overlap=50)
        assert len(chunks) == 1
        assert chunks[0]["text"] == para

    def test_paragraph_boundary_preferred(self):
        text = "\n".join([f"段落{i}" + "字" * 200 for i in range(6)])
        chunks = chunk_text(text, chunk_size=500, overlap=0)
        assert all("段落" in c["text"] for c in chunks)

    def test_empty_returns_empty(self):
        assert chunk_text("   \n\n  ") == []

    def test_heading_path_is_carried_and_long_paragraph_stays_intact(self):
        text = "# 产品手册\n\n## 部署\n\n" + "部署说明" * 150
        chunks = chunk_text(text, chunk_size=100, overlap=50)

        assert len(chunks) == 1
        assert chunks[0]["heading_path"] == ["产品手册", "部署"]
        assert chunks[0]["chapter"] == "产品手册"
        assert chunks[0]["title"] == "部署"
        assert chunks[0]["section"] == "产品手册 / 部署"
        assert chunks[0]["text"] == "部署说明" * 150

    def test_paragraph_boundary_preferred_over_target_size(self):
        text = "# 章节\n\n第一段" + "甲" * 80 + "\n\n第二段" + "乙" * 80
        chunks = chunk_text(text, chunk_size=100, overlap=0)

        assert len(chunks) == 2
        assert all("\n\n" not in chunk["text"] for chunk in chunks)


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

    def test_rerank_threshold_filters_low_scores(self, monkeypatch):
        import requests

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {"index": 0, "relevance_score": 0.91},
                        {"index": 1, "relevance_score": 0.22},
                    ]
                }

        monkeypatch.setattr(requests, "post", lambda *_a, **_k: Response())
        results = _rerank_siliconflow(
            "问题",
            [{"doc": "good", "seq": 0, "text": "相关"}, {"doc": "bad", "seq": 1, "text": "无关"}],
            threshold=0.5,
        )

        assert [(item["doc"], item["score"]) for item in results] == [("good", 0.91)]

    def test_keyword_recall_searches_section_metadata(self, monkeypatch):
        chunks = [{"doc": "manual.md", "seq": 0, "text": "部署内容", "section": "产品手册 / 发布"}]
        monkeypatch.setattr(retrieve_module, "_all_chunks", lambda: chunks)
        retrieve_module._keyword_index.cache_clear()

        results = retrieve_module._keyword_retrieve("发布", limit=5)

        assert results[0]["doc"] == "manual.md"
        retrieve_module._keyword_index.cache_clear()

    def test_missing_rerank_key_keeps_requested_top_k(self, monkeypatch):
        monkeypatch.setattr(retrieve_module.config, "SILICONFLOW_API_KEY", "")
        monkeypatch.setattr(
            retrieve_module,
            "_vector_retrieve",
            lambda *_a, **_k: [
                {"doc": f"doc-{index}", "seq": 0, "text": "x"}
                for index in range(4)
            ],
        )

        results = retrieve_module.retrieve("q", top_k=4, use_rerank=True, use_mixed=False)

        assert len(results) == 4
