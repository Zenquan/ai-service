"""RAG 融合层：把 RAG Core（server.core）与文档云存储（doc_store）组合成服务能力。

职责：
- 入库：解析 → 切块 → 向量化 → 切块同步存 MySQL 云存储
- 问答：检索 + 生成 + 引用校验（空库时友好回答）
- 运维：文档列表/删除、部署后从云存储重建向量索引、健康检查
- 全局锁：Qdrant local 模式单目录单进程访问，用 threading.Lock 串行化所有库操作，
  同时避免 FastEmbed 模型首次加载时多线程并发下载
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from server.core import config as rag_config
from server.core import main as rag_main
from server.core.chunker import chunk_text
from server.core.embed_store import delete_doc, list_docs, upsert_chunks
from server.core.ingest import parse_document
from server.core.retrieve import clear_cache, retrieve

logger = logging.getLogger(__name__)

__all__ = [
    "rag_main", "delete_doc", "list_docs", "clear_cache", "rag_config",
    "rag_lock", "UPLOAD_DIR",
]

# ── 全局锁：Qdrant local 单进程锁 + Embedding 首次加载并发保护 ──
rag_lock = threading.Lock()

# 前端上传文件的落盘目录：server 运行数据根（SERVER_HOME 或 cwd）下的 data/uploads
UPLOAD_DIR: Path = rag_config.SERVER_ROOT / "data" / "uploads"


def _ingest_one(path: Path, doc_name: str, stats: dict, doc_store) -> None:
    """单文件：解析 → 切块 → 向量化入库 → 切块 + 原始文件存 MySQL 云存储。"""
    raw = parse_document(path)
    chunks = chunk_text(raw, rag_config.CHUNK_SIZE, rag_config.CHUNK_OVERLAP)
    if not chunks:
        stats["skipped"].append({"name": doc_name, "reason": "解析后为空"})
        return
    inserted = upsert_chunks(chunks, doc_name)
    stats["total"] += inserted
    stats["docs"].append({"name": doc_name, "chunks": len(chunks), "inserted": inserted})
    # 云持久化：切块 + 原始文件字节（部署后可重新解析/切块）；MySQL 不可用时降级跳过
    if doc_store is not None:
        try:
            raw_bytes = path.read_bytes()
            doc_store.save_doc(doc_name, chunks, raw_bytes=raw_bytes)
        except (DocStoreUnavailable, OSError) as exc:
            logger.warning("文档云存储失败（跳过）: %s", exc)


def _collect_files(path: Path) -> list[Path]:
    """收集目录/文件下所有支持的文档（与 core.main 相同的扩展名白名单）。"""
    from server.core.main import SUPPORTED_EXTS

    if path.is_file():
        files = [path]
    else:
        files = sorted(
            p for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
        )
    return [f for f in files if f.suffix.lower() in SUPPORTED_EXTS]


def ingest_files(file_paths: list[Path]) -> dict:
    """入库若干已落盘的上传文件（逐个 ingest，单文件模式 doc_name=文件名）。

    返回 {"total", "docs", "skipped"} —— 与 rag_main.ingest 同构。
    每个文档的切块同步存 MySQL 云存储（部署后据此重建向量索引）。
    """
    from server.services.doc_store import DocStoreUnavailable, build_doc_store

    with rag_lock:
        stats = {"total": 0, "docs": [], "skipped": []}
        doc_store = build_doc_store()
        for p in file_paths:
            try:
                _ingest_one(p, p.name, stats, doc_store)
            except Exception as exc:  # noqa: BLE001 —— 单文档失败不中断批量
                stats["skipped"].append({"name": p.name, "reason": str(exc)})
        clear_cache()
        return stats


def ingest_dir(path: str) -> dict:
    """入库服务端目录（CLI 同款入口，供运维/调试）。"""
    from server.services.doc_store import DocStoreUnavailable, build_doc_store

    with rag_lock:
        target = Path(path)
        stats = {"total": 0, "docs": [], "skipped": []}
        files = _collect_files(target)
        doc_store = build_doc_store()
        for f in files:
            doc_name = str(f.relative_to(target)) if target.is_dir() else f.name
            try:
                _ingest_one(f, doc_name, stats, doc_store)
            except Exception as exc:  # noqa: BLE001
                stats["skipped"].append({"name": doc_name, "reason": str(exc)})
        clear_cache()
        return stats


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


def ask_stream(
    query: str,
    use_rerank: bool | None = None,
    top_k: int | None = None,
    rerank_threshold: float | None = None,
):
    """流式问答（SSE 用）：检索 → 逐 token 生成 → 引用校验，逐事件 yield。

    事件序列（dict）：
      {"event": "materials", "materials": [...]}        # 检索结果（生成前）
      {"event": "token", "text": "..."}                 # 生成 token 增量（多个）
      {"event": "done", "answer", "citations", "citation_valid"}  # 终态
      {"event": "error", "error": "..."}                # 失败（检索空/生成异常）
    """
    try:
        materials = retrieve(
            query,
            top_k=top_k,
            use_rerank=use_rerank,
            rerank_threshold=rerank_threshold,
        )
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "error": f"检索失败: {exc}"}
        return

    if not materials:
        yield {"event": "error", "error": "没有检索到相关素材（请先 ingest 入库）"}
        return

    yield {"event": "materials", "materials": materials}

    try:
        from server.core.generate import generate_stream

        answer_parts: list[str] = []
        for chunk in generate_stream(query, materials):
            if isinstance(chunk, str):
                answer_parts.append(chunk)
                yield {"event": "token", "text": chunk}
            else:
                yield {
                    "event": "done",
                    "answer": chunk["answer"],
                    "citations": chunk["citations"],
                    "citation_valid": chunk["valid"],
                    "material_count": chunk["material_count"],
                }
    except Exception as exc:  # noqa: BLE001
        yield {"event": "error", "error": f"生成失败: {exc}"}


def docs_list() -> list[dict]:
    """文档列表 [{doc, chunks}]。"""
    with rag_lock:
        return list_docs()


def docs_delete(doc_name: str) -> int:
    """删除单个文档，返回删除 chunk 数。同步删除 MySQL 云存储记录。"""
    from server.services.doc_store import DocStoreUnavailable, build_doc_store

    with rag_lock:
        n = delete_doc(doc_name)
        clear_cache()
        doc_store = build_doc_store()
        if doc_store is not None:
            try:
                doc_store.delete_doc(doc_name)
            except DocStoreUnavailable as exc:
                logger.warning("删除文档云存储记录失败（跳过）: %s", exc)
        return n


def restore_from_cloud() -> dict:
    """Qdrant 集合为空且 MySQL 有文档切块时，从云存储重建向量索引。

    场景：每次重新部署镜像，容器本地 Qdrant（server/qdrant_data）被清空；
    启动后调用本函数，从 MySQL 读回切块重新向量化入库（走远程 embedding）。
    返回 {"restored_docs": int, "restored_chunks": int, "error": str|None}。
    """
    from server.services.doc_store import DocStoreUnavailable, build_doc_store

    with rag_lock:
        result = {"restored_docs": 0, "restored_chunks": 0, "error": None}
        # 本地 Qdrant 已有文档则无需重建
        try:
            if list_docs():
                return result
        except Exception:  # noqa: BLE001 —— 集合不存在等视为空库，继续重建
            pass
        doc_store = build_doc_store()
        if doc_store is None:
            return result
        try:
            cloud_docs = doc_store.list_docs()
        except DocStoreUnavailable as exc:
            result["error"] = f"读取云文档失败: {exc}"
            return result
        for doc in cloud_docs:
            doc_name = doc["doc_name"]
            chunks = doc["chunks"]
            if not chunks:
                continue
            try:
                inserted = upsert_chunks(chunks, doc_name)
                result["restored_docs"] += 1
                result["restored_chunks"] += inserted
            except Exception as exc:  # noqa: BLE001 —— 单文档重建失败不中断
                logger.warning("重建文档 %s 失败: %s", doc_name, exc)
        clear_cache()
        return result


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
