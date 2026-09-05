"""向量化（FastEmbed 本地 / OpenAI 兼容远程 API）+ Qdrant 存储（local 免 Docker，可切远端 server）。

- Embedding：默认 fastembed 本地跑 config.EMBED_MODEL，首次使用自动下载模型；
  配置 config.EMBED_BASE_URL + EMBED_API_KEY 后走远程 /embeddings（镜像无需携带模型）
- Qdrant：默认 QdrantClient(path=...) local 模式；配 QDRANT_URL 即连远端 server（API 一致可平滑切换）
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    ScalarQuantization,
    ScalarQuantizationConfig,
    VectorParams,
)

# 量化类型枚举版本兼容：qdrant-client 旧版(≈1.9~1.12)叫 QuantizationType，新版(≥1.13)改名 ScalarType
try:
    from qdrant_client.models import ScalarType

    _QUANT_INT8 = ScalarType.INT8
except (ImportError, AttributeError):  # pragma: no cover —— 旧版客户端（1.9~1.12）
    from qdrant_client.models import QuantizationType

    _QUANT_INT8 = QuantizationType.INT8

from . import config


# ── Embedding ──────────────────────────────────────────────
_embedder = None


def get_embedder():
    """FastEmbed 单例（懒加载，模型首次使用自动下载）。"""
    global _embedder
    if _embedder is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # 云端精简镜像未装 fastembed
            raise RuntimeError(
                "本地向量化需要 fastembed，但当前环境未安装。"
                "云端部署请配置 EMBED_BASE_URL + EMBED_API_KEY 走远程 embedding（如硅基流动 /v1/embeddings）。"
            ) from exc

        _embedder = TextEmbedding(model_name=config.EMBED_MODEL)
    return _embedder


def _embed_remote(texts: list[str]) -> list[list[float]]:
    """调用 OpenAI 兼容 /embeddings（EMBED_BASE_URL + /embeddings）。"""
    import requests

    if not config.EMBED_API_KEY:
        raise RuntimeError(
            "EMBED_BASE_URL 已配置但缺少 EMBED_API_KEY；"
            "可显式配置 EMBED_API_KEY，或保证 rag/.env / 服务环境变量里有 SILICONFLOW_API_KEY。"
        )

    url = f"{config.EMBED_BASE_URL.rstrip('/')}/embeddings"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {config.EMBED_API_KEY}"},
        json={"model": config.EMBED_MODEL, "input": texts},
        timeout=60,
    )
    response.raise_for_status()
    rows = sorted(
        response.json().get("data", []),
        key=lambda item: item.get("index", 0),
    )
    vectors = [row.get("embedding", []) for row in rows]
    if len(vectors) != len(texts) or any(len(v) != config.EMBED_DIM for v in vectors):
        raise RuntimeError(
            f"远程 embedding 返回维度异常：期望 {len(texts)} 条 × {config.EMBED_DIM} 维，"
            f"实际 {len(vectors)} 条 × {[len(v) for v in vectors[:1]]}。"
            "检查 EMBED_MODEL 与 EMBED_DIM 是否匹配，并确认是否清库重 ingest。"
        )
    return [list(map(float, v)) for v in vectors]


# 远程 embedding 单次请求最多文本数（OpenAI 兼容端点对批量条数有限制）
_REMOTE_BATCH = 64


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化（本地 fastembed 或远程 API）。空文本返回空列表。"""
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return []
    if config.EMBED_BASE_URL:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _REMOTE_BATCH):
            vectors.extend(_embed_remote(texts[start:start + _REMOTE_BATCH]))
        return vectors
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
    """Qdrant 单例。

    - 配了 QDRANT_URL → 连远端 server（支持多进程并发，生产推荐）
    - 未配 QDRANT_URL → local 免 Docker（同一目录只允许一个进程）
    """
    global _client
    if _client is None:
        if config.QDRANT_URL:
            _client = QdrantClient(
                url=config.QDRANT_URL,
                api_key=config.QDRANT_API_KEY or None,
            )
        else:
            path = str(config.QDRANT_PATH)
            Path(path).mkdir(parents=True, exist_ok=True)
            try:
                _client = QdrantClient(path=path)
            except Exception:
                raise RuntimeError(
                    "qdrant_data 目录被另一个进程占用：Qdrant local 模式同一目录只允许一个进程访问。\n"
                    "请先结束其他正在运行的 python main.py ingest/ask 进程，再重试；"
                    "或设 QDRANT_URL 切远端 server 以支持并发。"
                )
    return _client


def _quantization_config():
    """按 QDRANT_QUANTIZATION 构建量化配置；留空返回 None（不量化）。"""
    if config.QDRANT_QUANTIZATION == "int8":
        return ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=_QUANT_INT8,
                always_ram=True,  # 常驻内存，避免量化后走 mmap 换页
            )
        )
    if config.QDRANT_QUANTIZATION:
        import warnings

        warnings.warn(
            f"QDRANT_QUANTIZATION={config.QDRANT_QUANTIZATION!r} 不是有效值（当前仅支持 'int8'），"
            "本次建集合将不启用量化。请检查 .env 配置。"
        )
    return None


def ensure_collection() -> None:
    """幂等建集合（dim/distance 与 embedding 对齐；量化仅对新建集合生效）。"""
    client = get_client()
    collections = [c.name for c in client.get_collections().collections]
    if config.COLLECTION not in collections:
        client.create_collection(
            collection_name=config.COLLECTION,
            vectors_config=VectorParams(size=config.EMBED_DIM, distance=Distance.COSINE),
            quantization_config=_quantization_config(),
        )


def _point_id(doc_name: str, seq: int) -> int:
    digest = hashlib.sha256(f"{doc_name}:{seq}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def upsert_chunks(chunks: list[dict], doc_name: str) -> int:
    """向量化 + 入库，并移除该文档已过期的旧 chunk。"""
    ensure_collection()
    client = get_client()
    texts = [c.get("embedding_text") or c["text"] for c in chunks]
    vectors = embed_texts(texts)
    if not vectors:
        return 0

    old_points, _ = client.scroll(
        config.COLLECTION,
        limit=2**31 - 1,
        with_payload=False,
        with_vectors=False,
        scroll_filter=Filter(must=[FieldCondition(key="doc", match=MatchValue(value=doc_name))]),
    )
    points = [
        PointStruct(
            id=_point_id(doc_name, c["seq"]),
            vector=v,
            payload={
                "doc": doc_name,
                "seq": c["seq"],
                "text": c["text"],
                "para_range": list(c.get("para_range", ())),
                "heading_path": list(c.get("heading_path", ())),
                "chapter": c.get("chapter", ""),
                "title": c.get("title", ""),
                "section": c.get("section", ""),
            },
        )
        for c, v in zip(chunks, vectors)
        if v
    ]
    client.upsert(collection_name=config.COLLECTION, points=points)
    new_ids = {point.id for point in points}
    stale_ids = [point.id for point in old_points if point.id not in new_ids]
    if stale_ids:
        client.delete(collection_name=config.COLLECTION, points_selector=stale_ids)
    return len(points)


# ── 文档级管理（P1：增量增删，不必全量重建）──────────────
# 注意：qdrant-client 1.9 的 scroll 参数是 scroll_filter（非 query_filter），limit 必填正整数


def list_docs() -> list[dict]:
    """列出库内所有文档及 chunk 数（按 doc 聚合）。"""
    client = get_client()
    if config.COLLECTION not in {c.name for c in client.get_collections().collections}:
        return []
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
    if config.COLLECTION not in {c.name for c in client.get_collections().collections}:
        return 0
    pts, _ = client.scroll(
        config.COLLECTION, limit=2**31 - 1, with_payload=True, with_vectors=False,
        scroll_filter=Filter(must=[FieldCondition(key="doc", match=MatchValue(value=doc_name))]),
    )
    if not pts:
        return 0
    ids = [p.id for p in pts]
    client.delete(collection_name=config.COLLECTION, points_selector=ids)
    return len(ids)
