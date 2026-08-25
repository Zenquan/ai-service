"""main.py：RAG 完整链路串起来的地方（可编程调用 + 命令行）。

链路（端到端）：
  ingest:  文档 → 解析(MinerU→markitdown→纯文本) → 清洗 → 切块 → FastEmbed 向量 → Qdrant 入库
  ask:     query → 向量化 → 相似检索 topK →(可选 rerank)→ 注入编号素材 → DeepSeek 生成 → 引用校验
  run:     ingest + ask 一条命令串起来（端到端演示）

用法：
  python main.py ingest  <docs_dir|file>
  python main.py ask     "<问题>" [--rerank] [--top-k N]
  python main.py run     <docs_dir> "<问题>" [--rerank]     # 一步到位
  python main.py eval
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import config
from chunker import chunk_text
from citations import verify_citations
from embed_store import (
    ensure_collection,
    upsert_chunks,
    delete_doc,
    list_docs,
)
from ingest import parse_document
from retrieve import retrieve, clear_cache
from generate import generate

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
def ask(query: str, use_rerank: bool | None = None, top_k: int | None = None) -> dict:
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
        result["materials"] = retrieve(query, top_k=top_k, use_rerank=use_rerank)
    except Exception as e:  # noqa: BLE001
        result["error"] = f"检索失败: {e}"
        return result

    if not result["materials"]:
        result["error"] = "没有检索到相关素材（请先 ingest 入库）"
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
def evaluate(cases_path: Path | None = None) -> dict:
    """种子问题评估：检索 topK 是否命中期望关键词。返回 {"hit", "total", "rate", "cases"}。"""
    cases: list = []
    if cases_path and cases_path.exists():
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not cases:
        cases = [
            {"question": "钱大妈的门店经营模式是什么？", "expect_doc": ["01-钱大妈日清模式.md"], "expect_kw": ["日清"]},
            {"question": "什么是 RAG 检索增强生成？", "expect_doc": ["02-RAG原理.md"], "expect_kw": ["检索", "向量"]},
        ]

    hits = 0
    detail = []
    for c in cases:
        mats = retrieve(c["question"])
        joined = " ".join(m["text"] for m in mats)
        doc_hit = not c.get("expect_doc") or any(d in " ".join(m["doc"] for m in mats) for d in c["expect_doc"])
        kw_hit = not c.get("expect_kw") or any(kw in joined for kw in c["expect_kw"])
        ok = doc_hit and kw_hit
        hits += 1 if ok else 0
        detail.append({
            "question": c["question"], "ok": ok,
            "doc_hit": doc_hit, "kw_hit": kw_hit,
            "expect": c.get("expect_doc") or c.get("expect_kw") or [],
        })

    return {"hit": hits, "total": len(cases), "rate": hits / len(cases) if cases else 0, "cases": detail}


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

    p_run = sub.add_parser("run", help="端到端：ingest + ask 一步到位")
    p_run.add_argument("path", help="文档目录或单个文件")
    p_run.add_argument("query", help="问题")
    p_run.add_argument("--no-rerank", action="store_true")

    sub.add_parser("eval", help="种子问题评估")
    sub.add_parser("doc-list", help="列出库内文档及 chunk 数")
    p_docdel = sub.add_parser("doc-delete", help="删除单个文档（不用全量重建）")
    p_docdel.add_argument("doc", help="文档名（如 rag-test-pdfs/DeepFace-ICCV2017.pdf）")

    args = parser.parse_args()
    if args.cmd == "ingest":
        print(_fmt_ingest(ingest(args.path)))
    elif args.cmd == "ask":
        # 反向：--no-rerank → use_rerank=False（None 走 config 默认开）
        ur = False if args.no_rerank else None
        print(_fmt_ask(ask(args.query, ur, args.top_k)))
    elif args.cmd == "run":
        ur = False if args.no_rerank else None
        print(_fmt_ingest(ingest(args.path)))
        print(_fmt_ask(ask(args.query, ur)))
    elif args.cmd == "eval":
        ev = evaluate(Path(__file__).parent / "data" / "eval_cases.json")
        for c in ev["cases"]:
            exp = c.get("expect_doc") or c.get("expect") or []
            print(f"  {'✅' if c['ok'] else '❌'} {c['question']}  (期望 {exp})")
        print(f"\n命中率: {ev['hit']}/{ev['total']} = {ev['rate'] * 100:.0f}%")
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