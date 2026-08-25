"""检索层：混合检索（关键词 BM25 式 + 向量相似）→ RRF 融合 →（可选 rerank）。

检索与生成解耦：换检索策略（纯向量/混合/rerank）只改本模块，不动生成端。

混合检索设计（面试可讲）：
- 向量召回：语义相近但词汇不重叠也能召回（"土豆" ↔ "马铃薯"）
- 关键词召回：精确术语/编号/专名命中更稳（"JFT-300M"、"DeepFace"）
- RRF（Reciprocal Rank Fusion）：按"排名倒数"加权融合两路结果，无需归一化分数，
  简单有效——recall 互补，精排交给 rerank。
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

from embed_store import embed_texts, get_client
import config


def retrieve(
    query: str,
    top_k: int | None = None,
    use_rerank: bool | None = None,
    use_mixed: bool | None = None,
) -> list[dict]:
    """混合检索：关键词 + 向量 → RRF 融合 → 可选 rerank。返回 [{text,doc,seq,score}] 降序。

    use_rerank=None → config.RERANK_DEFAULT；use_mixed=None → config.MIXED_DEFAULT。
    """
    top_k = top_k or config.TOP_K
    if use_rerank is None:
        use_rerank = config.RERANK_DEFAULT
    if use_mixed is None:
        use_mixed = config.MIXED_DEFAULT

    vec_results = _vector_retrieve(query, top_k * 2)     # 向量放宽召回，融合后再收紧
    kw_results = _keyword_retrieve(query, top_k * 2) if use_mixed else []

    results = _rrf_merge(vec_results, kw_results)[:top_k]

    if use_rerank and config.SILICONFLOW_API_KEY:
        try:
            results = _rerank_siliconflow(query, results)
        except Exception:  # noqa: BLE001 —— rerank 失败不崩检索，回退融合结果
            pass

    return results


# ── 向量召回 ─────────────────────────────────────────────
def _vector_retrieve(query: str, limit: int) -> list[dict]:
    vecs = embed_texts([query])
    if not vecs:
        return []
    client = get_client()
    hits = client.query_points(
        collection_name=config.COLLECTION,
        query=vecs[0],
        limit=limit,
        with_payload=True,
        with_vectors=False,
    ).points
    results = []
    for h in hits:
        p = h.payload or {}
        results.append({
            "text": p.get("text", ""),
            "doc": p.get("doc", ""),
            "seq": p.get("seq", 0),
            "score": float(h.score),
            "_rank": len(results),          # 保留召回序，RRF 用
        })
    return results


# ── 关键词召回（BM25 式：query 词项命中计数）──────────────
_CN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """切词：英文/数字单词 + 中文 2-gram（零依赖，可测试）。"""
    text = text.lower()
    tokens = _WORD.findall(text)
    for seq in _CN.findall(text):
        if len(seq) == 1:
            tokens.append(seq)
        else:
            tokens.extend(seq[i:i + 2] for i in range(len(seq) - 1))
    return tokens


@lru_cache(maxsize=1)
def _all_chunks() -> list[dict]:
    """全量 chunk 快照（内存缓存）。文档量小时直接遍历打分，够用且零依赖。"""
    client = get_client()
    pts, _ = client.scroll(
        config.COLLECTION, limit=2**31 - 1, with_payload=True, with_vectors=False
    )
    return [{"doc": p.payload.get("doc", ""), "seq": p.payload.get("seq", 0),
             "text": p.payload.get("text", "")} for p in pts]


def _clear_chunk_cache() -> None:
    _all_chunks.cache_clear()


def _keyword_retrieve(query: str, limit: int) -> list[dict]:
    """BM25 简化：query 词项命中计数打分（idf 加权略，计数已够小语料）。"""
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return []
    scored = []
    for c in _all_chunks():
        s = sum(_tokenize(c["text"]).count(t) for t in q_tokens)
        if s > 0:
            scored.append({**c, "score": float(s), "_rank": len(scored)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


# ── RRF 融合 ─────────────────────────────────────────────
def _rrf_merge(*lists: list[dict], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)，两路结果按排名融合。"""
    fused: dict[tuple[str, int], dict] = {}
    for ranked in lists:
        for i, item in enumerate(ranked):
            key = (item["doc"], item["seq"])
            if key not in fused:
                fused[key] = dict(item)
                fused[key]["rrf"] = 0.0
            fused[key]["rrf"] += 1.0 / (k + i + 1)
    out = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)
    for i, item in enumerate(out):
        item["score"] = item["rrf"]
        item.pop("_rank", None)
    return out


def clear_cache() -> None:
    """入库变更后清关键词召回缓存（main.ingest 里调用）。"""
    _clear_chunk_cache()


# ── rerank ───────────────────────────────────────────────
def _rerank_siliconflow(query: str, results: list[dict]) -> list[dict]:
    """硅基流动 bge-reranker：对融合结果精排，取前 RERANK_TOP_K。"""
    import requests

    resp = requests.post(
        config.RERANK_URL,
        headers={"Authorization": f"Bearer {config.SILICONFLOW_API_KEY}"},
        json={
            "model": config.RERANK_MODEL,
            "query": query,
            "documents": [r["text"] for r in results],
            "top_n": config.RERANK_TOP_K,
        },
        timeout=30,
    )
    resp.raise_for_status()

    ranked = []
    for item in resp.json().get("results", []):
        idx = item["index"]
        ranked.append({**results[idx], "score": float(item.get("relevance_score", 0))})
    return ranked