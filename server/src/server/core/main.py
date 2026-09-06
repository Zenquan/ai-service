"""main.py：RAG 完整链路串起来的地方（可编程调用 + 命令行）。

链路（端到端）：
  ingest:  文档 → 解析(MinerU→markitdown→纯文本) → 清洗 → 切块 → FastEmbed 向量 → Qdrant 入库
  ask:     query → 向量化 → 相似检索 topK →(可选 rerank)→ 注入编号素材 → DeepSeek 生成 → 引用校验
  run:     ingest + ask 一条命令串起来（端到端演示）

用法：
  python main.py ingest  <docs_dir|file>
  python main.py ask     "<问题>" [--no-rerank] [--top-k N] [--rerank-threshold X]
  python main.py run     <docs_dir> "<问题>" [--rerank]     # 一步到位
  python main.py eval
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config
from .chunker import chunk_text
from .citations import verify_citations
from .embed_store import (
    ensure_collection,
    upsert_chunks,
    delete_doc,
    list_docs,
)
from .ingest import parse_document
from .retrieve import retrieve, clear_cache
from .generate import generate

SUPPORTED_EXTS = {".pdf", ".txt", ".md", ".markdown", ".docx", ".html", ".jpg", ".png"}


# ── ① 入库链路 ─────────────────────────────────────────────
def ingest(path: str) -> dict:
    """解析 dir/文件 → 切块 → 向量化入库。幂等（稳定 id 覆盖同文档 chunk）。

    返回统计 {"total": int, "docs": [{"name", "chunks", "inserted"}], "skipped": [...]}
    """
    ensure_collection()
    target = Path(path)
    if target.is_file():
        files = [target]
    else:
        # 递归收集子目录下所有支持的文件（rglob）
        files = sorted(
            p for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
        )
    files = [f for f in files if f.suffix.lower() in SUPPORTED_EXTS]

    stats = {"total": 0, "docs": [], "skipped": []}
    if not files:
        return stats

    for f in files:
        try:
            doc_name = str(f.relative_to(target)) if target.is_dir() else f.name
            raw = parse_document(f)
            chunks = chunk_text(raw, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
            if not chunks:
                stats["skipped"].append({"name": doc_name, "reason": "解析后为空"})
                continue
            inserted = upsert_chunks(chunks, doc_name)
            stats["total"] += inserted
            stats["docs"].append({"name": doc_name, "chunks": len(chunks), "inserted": inserted})
        except Exception as e:  # noqa: BLE001 —— 单文档失败不中断批量
            stats["skipped"].append({"name": doc_name if 'doc_name' in locals() else f.name, "reason": str(e)})

    clear_cache()  # 入库后清关键词召回全量快照缓存
    return stats


# ── ② 问答链路 ─────────────────────────────────────────────
def ask(
    query: str,
    use_rerank: bool | None = None,
    top_k: int | None = None,
    rerank_threshold: float | None = None,
) -> dict:
    """检索 → 生成 → 引用校验，返回完整结果（可编程消费）。

    use_rerank=None → 默认启用 rerank（config.RERANK_DEFAULT；无 key 自动跳过）。
    返回 {
      "query", "materials": [{text,doc,seq,score}],
      "answer", "citations": [int], "citation_valid": bool, "material_count": int,
      "error": str|None   # 检索/生成失败时非空
    }
    """
    result = {"query": query, "materials": [], "answer": "", "citations": [],
              "citation_valid": False, "material_count": 0, "error": None}
    try:
        result["materials"] = retrieve(
            query,
            top_k=top_k,
            use_rerank=use_rerank,
            rerank_threshold=rerank_threshold,
        )
    except Exception as e:  # noqa: BLE001
        result["error"] = f"检索失败: {e}"
        return result

    if not result["materials"]:
        result["error"] = "知识库中没有检索到足够相关的资料"
        return result

    try:
        gen = generate(query, result["materials"])
    except Exception as e:  # noqa: BLE001
        result["error"] = f"生成失败: {e}"
        return result

    result.update({
        "answer": gen["answer"],
        "citations": gen["citations"],
        "citation_valid": gen["valid"],
        "material_count": gen["material_count"],
    })
    return result


# ── ③ 端到端：一条命令串起 ingest + ask ────────────────────
def run(docs_path: str, query: str, use_rerank: bool = False) -> dict:
    """ingest 后立即 ask（端到端演示）。返回 {"ingest": stats, "ask": result}。"""
    stats = ingest(docs_path)
    ans = ask(query, use_rerank=use_rerank)
    return {"ingest": stats, "ask": ans}


# ── ④ 评估 ─────────────────────────────────────────────────
def _doc_matches(actual: str, expected: str) -> bool:
    actual_path = Path(actual)
    expected_path = Path(expected)
    return actual == expected or actual_path.name == expected_path.name or actual.endswith(expected)


def _case_relevant(item: dict, case: dict) -> bool:
    labels = case.get("relevant") or case.get("relevant_chunks")
    if labels:
        return any(
            _doc_matches(item.get("doc", ""), label["doc"])
            and ("seq" not in label or item.get("seq") == label["seq"])
            for label in labels
        )
    expected_docs = case.get("relevant_docs") or case.get("expect_doc") or []
    if expected_docs:
        return any(_doc_matches(item.get("doc", ""), doc) for doc in expected_docs)
    expected_keywords = case.get("relevant_keywords") or case.get("expect_kw") or []
    text = item.get("text", "").lower()
    return any(keyword.lower() in text for keyword in expected_keywords)


def _case_label_count(case: dict) -> int:
    labels = case.get("relevant") or case.get("relevant_chunks")
    if labels:
        return len(labels)
    expected_docs = case.get("relevant_docs") or case.get("expect_doc") or []
    if expected_docs:
        return len(set(expected_docs))
    return 1 if case.get("relevant_keywords") or case.get("expect_kw") else 0


def _recall_at_k(mats: list[dict], case: dict, k: int) -> float:
    label_count = _case_label_count(case)
    if not label_count:
        return 0.0
    labels = case.get("relevant") or case.get("relevant_chunks")
    if labels:
        found = sum(
            1
            for label in labels
            if any(
                _doc_matches(item.get("doc", ""), label["doc"])
                and ("seq" not in label or item.get("seq") == label["seq"])
                for item in mats[:k]
            )
        )
    else:
        expected_docs = case.get("relevant_docs") or case.get("expect_doc") or []
        if expected_docs:
            found = sum(
                1
                for doc in set(expected_docs)
                if any(_doc_matches(item.get("doc", ""), doc) for item in mats[:k])
            )
        else:
            found = int(any(_case_relevant(item, case) for item in mats[:k]))
    return found / label_count


def evaluate(
    cases_path: Path | None = None,
    ks: tuple[int, ...] = (1, 3, 5),
    use_rerank: bool = False,
    rerank_threshold: float | None = None,
) -> dict:
    """离线评测标注集，返回 Recall@K、MRR 及逐题明细。"""
    cases: list = []
    if cases_path and cases_path.exists():
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not cases:
        cases = [
            {"question": "什么是 AI Agent？", "relevant_docs": ["01-AI-Agent入门.md"]},
            {"question": "什么是 RAG 检索增强生成？", "relevant_docs": ["02-RAG原理.md"]},
        ]

    ks = tuple(sorted({max(1, int(k)) for k in ks}))
    max_k = max(ks, default=config.TOP_K)
    hits = 0
    detail = []
    for c in cases:
        mats = retrieve(
            c["question"],
            top_k=max_k,
            use_rerank=use_rerank,
            rerank_threshold=rerank_threshold,
        )
        joined = " ".join(m["text"] for m in mats)
        expected_docs = c.get("relevant_docs") or c.get("expect_doc") or []
        expected_keywords = c.get("relevant_keywords") or c.get("expect_kw") or []
        doc_hit = not expected_docs or any(
            _doc_matches(m.get("doc", ""), doc) for m in mats for doc in expected_docs
        )
        kw_hit = not expected_keywords or any(keyword.lower() in joined.lower() for keyword in expected_keywords)
        ok = doc_hit and kw_hit
        hits += 1 if ok else 0
        recalls = {str(k): _recall_at_k(mats, c, k) for k in ks}
        relevant_ranks = [index + 1 for index, item in enumerate(mats) if _case_relevant(item, c)]
        detail.append({
            "question": c["question"], "ok": ok,
            "doc_hit": doc_hit, "kw_hit": kw_hit,
            "expect": expected_docs or expected_keywords,
            "recall_at_k": recalls,
            "first_relevant_rank": relevant_ranks[0] if relevant_ranks else None,
            "mrr": 1 / relevant_ranks[0] if relevant_ranks else 0.0,
        })

    recall_at_k = {
        str(k): sum(item["recall_at_k"][str(k)] for item in detail) / len(detail)
        if detail else 0.0
        for k in ks
    }
    result = {
        "hit": hits,
        "total": len(cases),
        "rate": hits / len(cases) if cases else 0,
        "recall_at_k": recall_at_k,
        "mrr": sum(item["mrr"] for item in detail) / len(detail) if detail else 0.0,
        "cases": detail,
    }
    result.update({f"recall@{k}": value for k, value in recall_at_k.items()})
    return result


# ── 命令行 ─────────────────────────────────────────────────
def _fmt_ingest(stats: dict) -> str:
    lines = [f"\n入库完成：共 {stats['total']} 条 chunk"]
    for d in stats["docs"]:
        lines.append(f"  ✅ {d['name']}: {d['chunks']} 块 → {d['inserted']} 条")
    for s in stats["skipped"]:
        lines.append(f"  ⏭  {s['name']}: {s['reason']}")
    return "\n".join(lines)


def _fmt_ask(ans: dict) -> str:
    if ans["error"]:
        return f"\n❌ {ans['error']}"
    lines = ["\n── 检索到的素材 ──"]
    for i, m in enumerate(ans["materials"], 1):
        lines.append(f"  [{i}] score={m['score']:.4f} 《{m['doc']}》 {m['text'][:60]}...")
    lines.append("\n── 回答 ──")
    lines.append(ans["answer"])
    flag = "✅" if ans["citation_valid"] else "❌ 含越界引用"
    lines.append(f"\n引用: {ans['citations']} | 引用合法: {flag}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="rag · 最小完整 RAG")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="解析文档并入库")
    p_ingest.add_argument("path", help="文档目录或单个文件")

    p_ask = sub.add_parser("ask", help="问答")
    p_ask.add_argument("query", help="问题")
    p_ask.add_argument("--no-rerank", action="store_true", help="关闭 rerank（默认开启）")
    p_ask.add_argument("--top-k", type=int, default=None, help="检索条数")
    p_ask.add_argument("--rerank-threshold", type=float, default=None, help="rerank 最低相关性分数")

    p_run = sub.add_parser("run", help="端到端：ingest + ask 一步到位")
    p_run.add_argument("path", help="文档目录或单个文件")
    p_run.add_argument("query", help="问题")
    p_run.add_argument("--no-rerank", action="store_true")

    p_eval = sub.add_parser("eval", help="离线评估 Recall@K / MRR")
    p_eval.add_argument("--rerank", action="store_true", help="评估时启用已配置的 rerank")
    p_eval.add_argument("--rerank-threshold", type=float, default=None, help="rerank 最低相关性分数")
    p_eval.add_argument("--ks", nargs="+", type=int, default=[1, 3, 5], help="计算哪些 K 值")
    sub.add_parser("doc-list", help="列出库内文档及 chunk 数")
    p_docdel = sub.add_parser("doc-delete", help="删除单个文档（不用全量重建）")
    p_docdel.add_argument("doc", help="文档名（如 rag-test-pdfs/DeepFace-ICCV2017.pdf）")

    args = parser.parse_args()
    if args.cmd == "ingest":
        print(_fmt_ingest(ingest(args.path)))
    elif args.cmd == "ask":
        # 反向：--no-rerank → use_rerank=False（None 走 config 默认开）
        ur = False if args.no_rerank else None
        print(_fmt_ask(ask(args.query, ur, args.top_k, args.rerank_threshold)))
    elif args.cmd == "run":
        ur = False if args.no_rerank else None
        print(_fmt_ingest(ingest(args.path)))
        print(_fmt_ask(ask(args.query, ur)))
    elif args.cmd == "eval":
        ev = evaluate(
            config.SERVER_ROOT / "data" / "eval_cases.json",
            ks=tuple(args.ks),
            use_rerank=args.rerank,
            rerank_threshold=args.rerank_threshold,
        )
        for c in ev["cases"]:
            exp = c.get("relevant_docs") or c.get("relevant") or c.get("expect_doc") or c.get("expect") or []
            print(f"  {'✅' if c['ok'] else '❌'} {c['question']}  (期望 {exp})")
        print(f"\n命中率: {ev['hit']}/{ev['total']} = {ev['rate'] * 100:.0f}%")
        print(" ".join(f"Recall@{k}={ev['recall_at_k'][str(k)]:.3f}" for k in args.ks))
        print(f"MRR={ev['mrr']:.3f}")
    elif args.cmd == "doc-list":
        docs = list_docs()
        if not docs:
            print("（库内暂无文档）")
        for d in docs:
            print(f"  {d['doc']}: {d['chunks']} chunks")
        print(f"\n共 {len(docs)} 份文档")
    elif args.cmd == "doc-delete":
        n = delete_doc(args.doc)
        clear_cache()
        print(f"✅ 已删除 {args.doc}（{n} 条 chunk）")


if __name__ == "__main__":
    main()
