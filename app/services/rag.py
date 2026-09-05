"""融合层：把 rag（独立 CLI 仓库）作为库导入 FastAPI。

设计要点（面试可讲）：
- rag 保持零改动：自己的 git 仓库、CLI（python main.py ingest/ask）、单测全独立
- FastAPI 只做"壳"：把 rag 目录加进 sys.path，导入它的模块（main/embed_store/retrieve）
- 模块名错开：rag 的核心入口叫 main，FastAPI 应用叫 app.main，互不冲突
- 全局锁：Qdrant local 模式单目录单进程访问，用 threading.Lock 串行化所有库操作，
  同时避免 FastEmbed 模型首次加载时多线程并发下载
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

# rag 目录（本次融合的目标库）
_RAG_DIR = Path(__file__).resolve().parents[2] / "rag"
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))

# ── 导入 rag 模块（必须在 sys.path 注入之后）──
import main as rag_main                        # noqa: E402  rag/main.py（ingest/ask/run/evaluate）
from embed_store import delete_doc, list_docs  # noqa: E402
from retrieve import clear_cache               # noqa: E402
import config as rag_config                    # noqa: E402

__all__ = [
    "rag_main", "delete_doc", "list_docs", "clear_cache", "rag_config",
    "rag_lock", "UPLOAD_DIR",
]

# ── 全局锁：Qdrant local 单进程锁 + Embedding 首次加载并发保护 ──
rag_lock = threading.Lock()

# 前端上传文件的落盘目录（相对 rag）
UPLOAD_DIR: Path = _RAG_DIR / "data" / "uploads"


def ingest_files(file_paths: list[Path]) -> dict:
    """入库若干已落盘的上传文件（逐个 ingest，单文件模式 doc_name=文件名）。

    返回 {"total", "docs", "skipped"} —— 与 rag_main.ingest 同构。
    """
    with rag_lock:
        stats = {"total": 0, "docs": [], "skipped": []}
        for p in file_paths:
            one = rag_main.ingest(str(p))          # 单文件模式
            stats["total"] += one.get("total", 0)
            stats["docs"].extend(one.get("docs", []))
            stats["skipped"].extend(one.get("skipped", []))
        clear_cache()
        return stats


def ingest_dir(path: str) -> dict:
    """入库服务端目录（CLI 同款入口，供运维/调试）。"""
    with rag_lock:
        return rag_main.ingest(path)


def ask(
    query: str,
    use_rerank: bool | None = None,
    top_k: int | None = None,
    rerank_threshold: float | None = None,
) -> dict:
    """问答（检索+生成+引用校验）。

    知识库为空（集合不存在 / 无文档）时返回友好回答而不是报错——
    前端展示为普通回答气泡，用户知道是「还没上传文档」，而不是「系统坏了」。
    """
    with rag_lock:
        result = rag_main.ask(
            query,
            use_rerank=use_rerank,
            top_k=top_k,
            rerank_threshold=rerank_threshold,
        )
        error = result.get("error")
        if error and _is_empty_kb_error(error):
            result["error"] = None
            result["answer"] = _EMPTY_KB_ANSWER
        return result


# 空库/集合不存在的识别特征（Qdrant local 与远端 server 的报错文案都含 "not found"）
_EMPTY_KB_ERROR_MARKERS = ("not found", "Collection")


def _is_empty_kb_error(error: str) -> bool:
    lowered = error.lower()
    return any(marker.lower() in lowered for marker in _EMPTY_KB_ERROR_MARKERS)


_EMPTY_KB_ANSWER = (
    "知识库还没有文档，我暂时无法回答这个问题。"
    "请先在「知识库管理」里上传文档（支持 PDF、Word、Markdown、图片等），"
    "上传完成后我就能基于文档内容回答并附上来源引用。"
)


def docs_list() -> list[dict]:
    """文档列表 [{doc, chunks}]。"""
    with rag_lock:
        return list_docs()


def docs_delete(doc_name: str) -> int:
    """删除单个文档，返回删除 chunk 数。"""
    with rag_lock:
        n = delete_doc(doc_name)
        clear_cache()
        return n


def health() -> dict:
    """健康检查 + 库统计（供前端状态栏/调试）。"""
    with rag_lock:
        docs = list_docs()
        return {
            "status": "ok",
            "docs": len(docs),
            "chunks": sum(d["chunks"] for d in docs),
            "embed_model": rag_config.EMBED_MODEL,
            "qdrant_path": str(rag_config.QDRANT_PATH),
            "collection": rag_config.COLLECTION,
            "rerank": bool(rag_config.SILICONFLOW_API_KEY),
            "llm_model": rag_config.LLM_MODEL,
        }
