"""chunk_size 对比实验：验证"用评测集调参"方法。

对每个候选 chunk_size：
  1. 解析 + 切块（用该 size）
  2. 清库重建 collection（Qdrant local 单进程，需串行）
  3. 入库
  4. 跑 eval_cases.json 评估
输出对比表，选出命中率最高的配置。

用法：.venv/bin/python exp_chunk_size.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from chunker import chunk_text
from embed_store import ensure_collection, get_client, upsert_chunks
from ingest import parse_document

# 候选 chunk_size（overlap 按 12% 随动）
CANDIDATES = [400, 800, 1500, 3000]
OVERLAP_RATIO = 0.12


def collect_files() -> list[Path]:
    target = Path(config.DATA_DIR)
    return sorted(
        p for p in target.rglob("*")
        if p.is_file() and p.suffix.lower() in
        {".pdf", ".txt", ".md", ".markdown", ".docx", ".html", ".jpg", ".png"}
    )


def load_cases() -> list[dict]:
    p = Path(__file__).parent / "data" / "eval_cases.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def evaluate_at(current: dict, chunk_size: int) -> dict:
    """清库 → 用指定 size 重新入库 → 评估。返回 {hit, total, rate, detail}。"""
    overlap = max(1, int(chunk_size * OVERLAP_RATIO))
    print(f"\n── 实验 chunk_size={chunk_size} overlap={overlap} ──")

    # 1. 清库重建
    client = get_client()
    try:
        client.delete_collection(config.COLLECTION)
    except Exception:  # noqa: BLE001
        pass
    ensure_collection()

    # 2. 逐文件解析 + 切块 + 入库
    total_chunks = 0
    for f in collect_files():
        text = parse_document(f)
        chunks = chunk_text(text, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            print(f"  ⏭ {f.name}: 无内容")
            continue
        n = upsert_chunks(chunks, f.name)
        total_chunks += n
    print(f"  入库 chunk 数: {total_chunks}")

    # 3. 评估（检索 topK：期望文档命中 + 期望关键词命中）
    cases = load_cases()
    hits = 0
    detail = []
    for c in cases:
        from retrieve import retrieve
        mats = retrieve(c["question"])
        joined = " ".join(m["text"] for m in mats)
        doc_hit = not c.get("expect_doc") or any(d in " ".join(m["doc"] for m in mats) for d in c["expect_doc"])
        kw_hit = not c.get("expect_kw") or any(kw in joined for kw in c["expect_kw"])
        ok = doc_hit and kw_hit
        hits += 1 if ok else 0
        detail.append({"question": c["question"], "ok": ok, "mats": len(mats)})
    return {"hit": hits, "total": len(cases), "rate": hits / len(cases), "detail": detail}


def main() -> None:
    results = []
    for size in CANDIDATES:
        r = evaluate_at({}, size)
        results.append({"chunk_size": size, **r})
        print(f"  → 命中 {r['hit']}/{r['total']} = {r['rate']*100:.0f}%")
        for d in r["detail"]:
            print(f"      {'✅' if d['ok'] else '❌'} {d['question']} (检索 {d['mats']} 条)")

    print("\n═══ 对比汇总 ═══")
    best = max(results, key=lambda x: x["rate"])
    for r in results:
        mark = " 👑最优" if r["chunk_size"] == best["chunk_size"] else ""
        print(f"  chunk_size={r['chunk_size']:<5} 命中率 {r['rate']*100:>3.0f}%  ({r['hit']}/{r['total']}){mark}")
    print(f"\n建议: chunk_size={best['chunk_size']}")


if __name__ == "__main__":
    main()