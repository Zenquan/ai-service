"""FastAPI 接口层测试：health / docs / ingest / ask 路由（打桩 rag 服务层，不连 Qdrant/LLM）。

覆盖重点：
- 路由语义：状态码、响应结构
- ingest：白名单校验、防路径穿越、错误聚合
- docs 删除：成功 / 404
- ask：参数校验（Pydantic）、业务错误透传
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # fastapi-app 根

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import rag as rag_service


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    """把上传落盘目录指到临时目录，测试不污染真实 rag/data/uploads。"""
    monkeypatch.setattr(rag_service, "UPLOAD_DIR", tmp_path / "uploads")
    return tmp_path / "uploads"


# ── health ──────────────────────────────────────────────────
class TestHealth:
    def test_ok(self, client, monkeypatch):
        monkeypatch.setattr(
            rag_service, "health",
            lambda: {"status": "ok", "docs": 5, "chunks": 155, "embed_model": "m",
                     "rerank": True, "llm_model": "d"},
        )
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["docs"] == 5


# ── docs ────────────────────────────────────────────────────
class TestDocs:
    def test_list_ok(self, client, monkeypatch):
        monkeypatch.setattr(rag_service, "docs_list",
                            lambda: [{"doc": "a.md", "chunks": 1}, {"doc": "b.pdf", "chunks": 3}])
        r = client.get("/api/v1/docs")
        assert r.status_code == 200
        assert r.json() == [{"doc": "a.md", "chunks": 1}, {"doc": "b.pdf", "chunks": 3}]

    def test_delete_ok(self, client, monkeypatch):
        monkeypatch.setattr(rag_service, "docs_delete", lambda name: 7)
        r = client.delete("/api/v1/docs/a.md")
        assert r.status_code == 200
        assert r.json() == {"deleted": 7, "doc": "a.md"}

    def test_delete_not_found_404(self, client, monkeypatch):
        monkeypatch.setattr(rag_service, "docs_delete", lambda name: 0)
        r = client.delete("/api/v1/docs/nope.md")
        assert r.status_code == 404
        assert "nope.md" in r.json()["detail"]


# ── ingest：上传 ────────────────────────────────────────────
class TestIngest:
    def test_md_upload_ok(self, client, tmp_path, monkeypatch, upload_dir):
        monkeypatch.setattr(rag_service, "ingest_files",
                            lambda paths: {"total": 1, "docs": [{"name": "a.md", "chunks": 1, "inserted": 1}],
                                           "skipped": [], "errors": []})
        f = tmp_path / "a.md"
        f.write_text("内容", encoding="utf-8")
        with f.open("rb") as fh:
            r = client.post("/api/v1/ingest", files=[("files", ("a.md", fh, "text/markdown"))])
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1 and body["docs"][0]["name"] == "a.md"

    def test_unsupported_ext_400(self, client, tmp_path, upload_dir):
        f = tmp_path / "evil.exe"
        f.write_bytes(b"MZ")
        with f.open("rb") as fh:
            r = client.post("/api/v1/ingest", files=[("files", ("evil.exe", fh, "application/octet-stream"))])
        assert r.status_code == 400
        detail = r.json()["detail"]["errors"][0]
        assert "不支持的格式" in detail["reason"]

    def test_path_traversal_name_sanitized(self, client, tmp_path, monkeypatch, upload_dir):
        """filename 带路径（../）→ 落盘时只取 basename。"""
        captured = {}

        def fake_ingest(paths):
            captured["paths"] = [str(p) for p in paths]
            return {"total": 0, "docs": [], "skipped": [], "errors": []}

        monkeypatch.setattr(rag_service, "ingest_files", fake_ingest)
        f = tmp_path / "safe.md"
        f.write_text("x", encoding="utf-8")
        with f.open("rb") as fh:
            r = client.post("/api/v1/ingest",
                            files=[("files", ("../../evil.md", fh, "text/markdown"))])
        assert r.status_code == 200
        # 落盘路径应只有 evil.md（无 ../），且落在临时 upload_dir
        assert captured["paths"] == [str(upload_dir / "evil.md")]

    def test_mixed_ok_and_bad(self, client, tmp_path, monkeypatch, upload_dir):
        """一个合法 + 一个非法文件：合法的入库，非法的进 errors，不整体 400。"""
        monkeypatch.setattr(rag_service, "ingest_files",
                            lambda paths: {"total": 1, "docs": [{"name": "ok.md", "chunks": 1, "inserted": 1}],
                                           "skipped": [], "errors": []})
        ok = tmp_path / "ok.md"
        ok.write_text("x", encoding="utf-8")
        files = [
            ("files", ("ok.md", ok.open("rb"), "text/markdown")),
            ("files", ("bad.xyz", b"zzz", "application/octet-stream")),
        ]
        r = client.post("/api/v1/ingest", files=files)
        assert r.status_code == 200
        assert r.json()["errors"][0]["name"] == "bad.xyz"

    def test_no_files_400(self, client):
        r = client.post("/api/v1/ingest")
        assert r.status_code == 422  # FastAPI 校验：files 必填


# ── ask ─────────────────────────────────────────────────────
class TestAsk:
    def test_ok_full(self, client, monkeypatch):
        monkeypatch.setattr(
            rag_service, "ask",
            lambda q, use_rerank=None, top_k=None: {
                "query": q, "materials": [], "answer": "答[来源1]", "citations": [1],
                "citation_valid": True, "material_count": 1, "error": None,
            },
        )
        r = client.post("/api/v1/ask", json={"query": "问题？"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer"] == "答[来源1]" and body["citation_valid"] is True

    def test_business_error_in_error_field(self, client, monkeypatch):
        """检索无素材 → 非 HTTP 错误，走 error 字段。"""
        monkeypatch.setattr(
            rag_service, "ask",
            lambda q, use_rerank=None, top_k=None: {
                "query": q, "materials": [], "answer": "", "citations": [],
                "citation_valid": False, "material_count": 0,
                "error": "没有检索到相关素材（请先 ingest 入库）",
            },
        )
        r = client.post("/api/v1/ask", json={"query": "问题？"})
        assert r.status_code == 200
        assert "没有检索到" in r.json()["error"]

    def test_rerank_flag_passed_through(self, client, monkeypatch):
        captured = {}

        def fake_ask(q, use_rerank=None, top_k=None):
            captured.update(use_rerank=use_rerank, top_k=top_k)
            return {"query": q, "materials": [], "answer": "", "citations": [],
                    "citation_valid": False, "material_count": 0, "error": None}

        monkeypatch.setattr(rag_service, "ask", fake_ask)
        client.post("/api/v1/ask", json={"query": "q", "use_rerank": False, "top_k": 8})
        assert captured["use_rerank"] is False and captured["top_k"] == 8

    def test_empty_query_422(self, client):
        r = client.post("/api/v1/ask", json={"query": ""})
        assert r.status_code == 422

    def test_internal_error_500(self, client, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("LLM down")

        monkeypatch.setattr(rag_service, "ask", boom)
        r = client.post("/api/v1/ask", json={"query": "问题？"})
        assert r.status_code == 500