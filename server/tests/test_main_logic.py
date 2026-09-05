"""main 链路逻辑测试：ask 错误分支 / evaluate 命中率 / generate Prompt 组装。

设计：打桩 retrieve / generate，不连 Qdrant、不调真实 LLM，全部离线可跑。
"""
from __future__ import annotations

import json

import pytest

from server.core import main as rag_main  # noqa: E402
from server.core.generate import _build_user_prompt  # noqa: E402

# ── ask 错误分支 ─────────────────────────────────────────────
class TestAskErrorBranches:
    def test_retrieve_failure_sets_error(self, monkeypatch):
        """检索抛异常 → error 字段（不崩溃）。"""

        def boom(_q, *_a, **_k):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(rag_main, "retrieve", boom)
        out = rag_main.ask("问题")
        assert out["error"] and "检索失败" in out["error"]
        assert out["materials"] == []

    def test_no_materials_sets_error(self, monkeypatch):
        """检索无结果 → 引导先入库。"""
        monkeypatch.setattr(rag_main, "retrieve", lambda *_a, **_k: [])
        out = rag_main.ask("问题")
        assert "没有检索到足够相关的资料" in out["error"]

    def test_generate_failure_sets_error(self, monkeypatch):
        """生成失败 → error 字段。"""
        monkeypatch.setattr(rag_main, "retrieve", lambda *_a, **_k: [{"text": "t", "doc": "d", "seq": 0}])
        monkeypatch.setattr(rag_main, "generate", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("LLM timeout")))
        out = rag_main.ask("问题")
        assert "生成失败" in out["error"]

    def test_success_returns_full_shape(self, monkeypatch):
        """成功路径：answer/citations/valid/material_count 齐全。"""
        monkeypatch.setattr(
            rag_main, "retrieve",
            lambda *_a, **_k: [{"text": "素材", "doc": "d.md", "seq": 0}],
        )
        monkeypatch.setattr(
            rag_main, "generate",
            lambda *_a, **_k: {"answer": "回答[来源1]", "citations": [1], "valid": True, "material_count": 1},
        )
        out = rag_main.ask("问题")
        assert out["answer"] == "回答[来源1]"
        assert out["citations"] == [1]
        assert out["citation_valid"] is True
        assert out["material_count"] == 1
        assert out["error"] is None

# ── evaluate 命中率 ─────────────────────────────────────────
class TestEvaluate:
    def _eval(self, tmp_path, cases, monkeypatch):
        f = tmp_path / "cases.json"
        f.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        return rag_main.evaluate(f)

    def test_all_hit(self, tmp_path, monkeypatch):
        """双条件（doc + kw）全部命中 → 100%。"""
        monkeypatch.setattr(
            rag_main, "retrieve",
            lambda *_a, **_k: [{"text": "日清模式正文", "doc": "01-钱大妈日清模式.md", "seq": 0}],
        )
        cases = [{"question": "q1", "expect_doc": ["01-钱大妈日清模式.md"], "expect_kw": ["日清"]}]
        out = self._eval(tmp_path, cases, monkeypatch)
        assert out["hit"] == 1 and out["rate"] == 1.0
        assert out["cases"][0]["ok"] is True

    def test_doc_miss_marks_fail(self, tmp_path, monkeypatch):
        """文档不命中 → 该用例失败。"""
        monkeypatch.setattr(
            rag_main, "retrieve",
            lambda *_a, **_k: [{"text": "无关内容", "doc": "other.md", "seq": 0}],
        )
        cases = [{"question": "q1", "expect_doc": ["目标.pdf"], "expect_kw": ["关键词"]}]
        out = self._eval(tmp_path, cases, monkeypatch)
        assert out["hit"] == 0 and out["cases"][0]["ok"] is False

    def test_empty_cases_falls_back_to_builtin(self, tmp_path, monkeypatch):
        """空用例文件 → 使用内置默认用例（钱大妈 + RAG 原理），API 不崩。"""
        monkeypatch.setattr(rag_main, "retrieve", lambda *_a, **_k: [])
        out = self._eval(tmp_path, [], monkeypatch)
        assert out["total"] == 2  # 内置默认用例数
        assert out["rate"] == 0

    def test_reports_recall_at_k_and_mrr(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            rag_main,
            "retrieve",
            lambda *_a, **_k: [
                {"text": "无关", "doc": "other.md", "seq": 0},
                {"text": "命中", "doc": "target.md", "seq": 2},
            ],
        )
        cases = [{"question": "q1", "relevant": [{"doc": "target.md", "seq": 2}]}]

        out = self._eval(tmp_path, cases, monkeypatch)

        assert out["recall_at_k"]["1"] == 0.0
        assert out["recall_at_k"]["3"] == 1.0
        assert out["mrr"] == 0.5
        assert out["cases"][0]["first_relevant_rank"] == 2

# ── generate Prompt 组装 ────────────────────────────────────
class TestGeneratePrompt:
    def test_prompt_contains_numbered_sources_and_question(self):
        mats = [
            {"text": "素材一", "doc": "a.md", "seq": 0, "section": "产品 / 部署"},
            {"text": "素材二", "doc": "b.md", "seq": 1},
        ]
        prompt = _build_user_prompt("我的问题？", mats)
        assert "[来源1]" in prompt and "[来源2]" in prompt
        assert "（来自《a.md》" in prompt
        assert "章节：产品 / 部署" in prompt
        assert "我的问题？" in prompt

    def test_prompt_no_materials_keeps_sections(self):
        prompt = _build_user_prompt("q", [])
        assert "【资料】" in prompt and "【问题】" in prompt and "q" in prompt
