"""混合检索：向量召回 + BM25 关键词召回 + RRF 融合 + 可选 rerank。"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from math import log

import config
from embed_store import embed_texts, get_client


def retrieve(
    query: str,
    top_k: int | None = None,
    use_rerank: bool | None = None,
    use_mixed: bool | None = None,
    rerank_threshold: float | None = None,
) -> list[dict]:
    """双路召回后 RRF 融合，再按需精排。"""
    top_k = top_k or config.TOP_K
    if use_rerank is None:
        use_rerank = config.RERANK_DEFAULT
    if use_mixed is None:
        use_mixed = config.MIXED_DEFAULT

    candidate_limit = max(top_k, top_k * config.RETRIEVAL_CANDIDATE_MULTIPLIER)
    vector_results = _vector_retrieve(query, candidate_limit)
    keyword_results = _keyword_retrieve(query, candidate_limit) if use_mixed else []
    results = _rrf_merge(vector_results, keyword_results, k=config.RRF_K)
    rerank_applied = False

    if use_rerank and config.SILICONFLOW_API_KEY and results:
        try:
            results = _rerank_siliconflow(
                query,
                results[:max(top_k, config.RERANK_TOP_K)],
                threshold=config.RERANK_SCORE_THRESHOLD if rerank_threshold is None else rerank_threshold,
            )
            rerank_applied = True
        except Exception:  # noqa: BLE001
            pass

    result_limit = config.RERANK_TOP_K if rerank_applied else top_k
    return results[:result_limit]


def _vector_retrieve(query: str, limit: int) -> list[dict]:
    vectors = embed_texts([query])
    if not vectors:
        return []
    hits = get_client().query_points(
        collection_name=config.COLLECTION,
        query=vectors[0],
        limit=limit,
        with_payload=True,
        with_vectors=False,
    ).points
    results = []
    for hit in hits:
        payload = hit.payload or {}
        results.append({
            "text": payload.get("text", ""),
            "doc": payload.get("doc", ""),
            "seq": payload.get("seq", 0),
            "score": float(hit.score),
            "vector_score": float(hit.score),
            "chapter": payload.get("chapter", ""),
            "title": payload.get("title", ""),
            "section": payload.get("section", ""),
            "heading_path": payload.get("heading_path", []),
            "_rank": len(results),
        })
    return results


_CN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """英文/数字按词切分，中文按二元词切分。"""
    text = text.lower()
    tokens = _WORD.findall(text)
    for sequence in _CN.findall(text):
        if len(sequence) == 1:
            tokens.append(sequence)
        else:
            tokens.extend(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return tokens


@lru_cache(maxsize=1)
def _all_chunks() -> list[dict]:
    """缓存 Qdrant payload 快照，入库或删除后由 clear_cache 失效。"""
    points, _ = get_client().scroll(
        config.COLLECTION,
        limit=2**31 - 1,
        with_payload=True,
        with_vectors=False,
    )
    return [
        {
            "doc": (point.payload or {}).get("doc", ""),
            "seq": (point.payload or {}).get("seq", 0),
            "text": (point.payload or {}).get("text", ""),
            "chapter": (point.payload or {}).get("chapter", ""),
            "title": (point.payload or {}).get("title", ""),
            "section": (point.payload or {}).get("section", ""),
            "heading_path": (point.payload or {}).get("heading_path", []),
        }
        for point in points
    ]


@lru_cache(maxsize=1)
def _keyword_index() -> dict:
    """构建轻量倒排索引，避免查询时重复扫描和重复分词。"""
    chunks = _all_chunks()
    postings: dict[str, list[tuple[int, int, int]]] = {}
    lengths: list[int] = []
    document_frequency: Counter[str] = Counter()
    for chunk_index, chunk in enumerate(chunks):
        body_counts = Counter(_tokenize(chunk["text"]))
        title_counts = Counter(_tokenize(chunk.get("section", "")))
        lengths.append(sum(body_counts.values()) or 1)
        for token in body_counts.keys() | title_counts.keys():
            document_frequency[token] += 1
            postings.setdefault(token, []).append(
                (chunk_index, body_counts.get(token, 0), title_counts.get(token, 0))
            )
    return {
        "chunks": chunks,
        "postings": postings,
        "document_frequency": document_frequency,
        "lengths": lengths,
        "avg_length": sum(lengths) / len(lengths) if lengths else 1.0,
    }


def _keyword_retrieve(query: str, limit: int) -> list[dict]:
    """BM25 关键词召回，章节标题命中按 2 倍词频计权。"""
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return []
    index = _keyword_index()
    total = len(index["chunks"])
    scores: dict[int, float] = {}
    for token in query_tokens:
        document_frequency = index["document_frequency"].get(token, 0)
        if not document_frequency:
            continue
        idf = log(1 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
        for chunk_index, body_tf, title_tf in index["postings"].get(token, []):
            term_frequency = body_tf + 2 * title_tf
            if not term_frequency:
                continue
            length = index["lengths"][chunk_index]
            norm = config.BM25_K1 * (
                1 - config.BM25_B + config.BM25_B * length / index["avg_length"]
            )
            scores[chunk_index] = scores.get(chunk_index, 0.0) + idf * (
                term_frequency * (config.BM25_K1 + 1) / (term_frequency + norm)
            )
    ranked = sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            index["chunks"][item[0]]["doc"],
            index["chunks"][item[0]]["seq"],
        ),
    )[:limit]
    results = [{**index["chunks"][chunk_index], "score": score, "keyword_score": score}
               for chunk_index, score in ranked]
    for rank, item in enumerate(results):
        item["_rank"] = rank
    return results


def _rrf_merge(*lists: list[dict], k: int = 60) -> list[dict]:
    """RRF：对每路结果按 1 / (k + rank) 融合，不混加不同量纲的原始分数。"""
    fused: dict[tuple[str, int], dict] = {}
    for ranked in lists:
        for rank, item in enumerate(ranked, start=1):
            key = (item["doc"], item["seq"])
            if key not in fused:
                fused[key] = dict(item)
                fused[key]["rrf"] = 0.0
            else:
                for field, value in item.items():
                    if field not in fused[key] or not fused[key][field]:
                        fused[key][field] = value
            fused[key]["rrf"] += 1.0 / (k + rank)
    results = sorted(fused.values(), key=lambda item: (-item["rrf"], item["doc"], item["seq"]))
    for rank, item in enumerate(results):
        item["score"] = item["rrf"]
        item["rrf_rank"] = rank
        item.pop("_rank", None)
    return results


def clear_cache() -> None:
    """入库或删除文档后清理关键词快照和倒排索引。"""
    _all_chunks.cache_clear()
    _keyword_index.cache_clear()


def _rerank_siliconflow(
    query: str,
    results: list[dict],
    threshold: float = 0.0,
) -> list[dict]:
    """调用 bge-reranker，并按 relevance_score 过滤低于阈值的候选。"""
    import requests

    response = requests.post(
        config.RERANK_URL,
        headers={"Authorization": f"Bearer {config.SILICONFLOW_API_KEY}"},
        json={
            "model": config.RERANK_MODEL,
            "query": query,
            "documents": [result["text"] for result in results],
            "top_n": min(config.RERANK_TOP_K, len(results)),
        },
        timeout=30,
    )
    response.raise_for_status()

    ranked = []
    for item in response.json().get("results", []):
        index = item["index"]
        ranked.append({**results[index], "score": float(item.get("relevance_score", 0))})
    if threshold > 0:
        ranked = [item for item in ranked if item["score"] >= threshold]
    return ranked
