"""向量化（FastEmbed 本地）+ Qdrant 存储（local 免 Docker）。

- Embedding：fastembed 本地跑 bge-small-zh-v1.5，首次使用自动下载模型（约 150MB）
- Qdrant：QdrantClient(path=...) local 模式，与生产远端代码同 API（可平滑切换）
"""
from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

import config


# ── Embedding ──────────────────────────────────────────────
_embedder = None


def get_embedder():
    """FastEmbed 单例（懒加载，模型首次使用自动下载）。"""
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(model_name=config.EMBED_MODEL)
    return _embedder


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。空文本返回空列表。"""
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return []
    return [v.tolist() for v in get_embedder().embed(texts)]


# ── Qdrant ─────────────────────────────────────────────────
_client = None


def _close_client() -> None:
    """atexit 显式关闭，避免解释器退出时 QdrantClient.__del__ 告警（sys.meta_path is None）。"""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001 —— 关闭失败无害
            pass
        _client = None


import atexit  # noqa: E402
atexit.register(_close_client)


def get_client() -> QdrantClient:
    """Qdrant 单例。local 免 Docker；改远端只需换构造参数。"""
    global _client
    if _client is None:
        path = str(config.QDRANT_PATH)
        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            _client = QdrantClient(path=path)
        except Exception:
            raise RuntimeError(
                "qdrant_data 目录被另一个进程占用：Qdrant local 模式同一目录只允许一个进程访问。\n"
                "请先结束其他正在运行的 python main.py ingest/ask 进程，再重试。"
            )
    return _client


def ensure_collection() -> None:
    """幂等建集合（dim/distance 与 embedding 对齐）。"""
    client = get_client()
    collections = [c.name for c in client.get_collections().collections]
    if config.COLLECTION not in collections:
        client.create_collection(
            collection_name=config.COLLECTION,
            vectors_config=VectorParams(size=config.EMBED_DIM, distance=Distance.COSINE),
        )


def upsert_chunks(chunks: list[dict], doc_name: str) -> int:
    """向量化 + 入库。chunks: [{text, seq, para_range}] → 返回入库数。"""
    ensure_collection()
    client = get_client()
    texts = [c["text"] for c in chunks]
    vectors = embed_texts(texts)
    if not vectors:
        return 0

    points = [
        PointStruct(
            id=abs(hash(f"{doc_name}:{c['seq']}")) % (2**63),  # 稳定 id（同文档同 chunk 幂等）
            vector=v,
            payload={
                "doc": doc_name,
                "seq": c["seq"],
                "text": c["text"],
                "para_range": list(c.get("para_range", ())),
            },
        )
        for c, v in zip(chunks, vectors)
        if v
    ]
    client.upsert(collection_name=config.COLLECTION, points=points)
    return len(points)


# ── 文档级管理（P1：增量增删，不必全量重建）──────────────
# 注意：qdrant-client 1.9 的 scroll 参数是 scroll_filter（非 query_filter），limit 必填正整数


def list_docs() -> list[dict]:
    """列出库内所有文档及 chunk 数（按 doc 聚合）。"""
    client = get_client()
    pts, _ = client.scroll(
        config.COLLECTION, limit=2**31 - 1, with_payload=True, with_vectors=False
    )
    agg: dict[str, int] = {}
    for p in pts:
        doc = (p.payload or {}).get("doc", "?")
        agg[doc] = agg.get(doc, 0) + 1
    return [{"doc": d, "chunks": n} for d, n in sorted(agg.items())]


def delete_doc(doc_name: str) -> int:
    """删除单个文档的全部 chunk（按 payload.doc 过滤）。返回删除条数。"""
    client = get_client()
    pts, _ = client.scroll(
        config.COLLECTION, limit=2**31 - 1, with_payload=True, with_vectors=False,
        scroll_filter=Filter(must=[FieldCondition(key="doc", match=MatchValue(value=doc_name))]),
    )
    if not pts:
        return 0
    ids = [p.id for p in pts]
    client.delete(collection_name=config.COLLECTION, points_selector=ids)
    return len(ids)