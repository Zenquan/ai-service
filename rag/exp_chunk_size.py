"""结构感知切块粒度对比实验。

对每个候选 chunk_size：
  1. 解析 + 结构感知切块（用该 size 作为段落聚合目标）
  2. 清库重建 collection（Qdrant local 单进程，需串行）
  3. 入库
  4. 跑 eval_cases.json，输出 Recall@K / MRR

用法：.venv/bin/python exp_chunk_size.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from chunker import chunk_text
from embed_store import ensure_collection, get_client, upsert_chunks
from ingest import parse_document
from main import evaluate
from retrieve import clear_cache

# 候选 chunk_size（仅控制同章节完整段落的聚合目标）
CANDIDATES = [300, 600, 900, 1200]


def collect_files() -> list[Path]:
    target = Path(config.DATA_DIR)
    return sorted(
        p for p in target.rglob("*")
        if p.is_file() and p.suffix.lower() in
        {".pdf", ".txt", ".md", ".markdown", ".docx", ".html", ".jpg", ".png"}
    )


def evaluate_at(chunk_size: int) -> dict:
    """清库 → 用指定粒度重建 → 离线评估。"""
    print(f"\n── 实验 chunk_size={chunk_size}（段落聚合目标）──")

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
        chunks = chunk_text(text, chunk_size=chunk_size, overlap=0)
        if not chunks:
            print(f"  ⏭ {f.name}: 无内容")
            continue
        n = upsert_chunks(chunks, f.relative_to(config.DATA_DIR).as_posix())
        total_chunks += n
    print(f"  入库 chunk 数: {total_chunks}")
    clear_cache()

    return evaluate(Path(__file__).parent / "data" / "eval_cases.json", use_rerank=False)


def main() -> None:
    results = []
    for size in CANDIDATES:
        r = evaluate_at(size)
        results.append({"chunk_size": size, **r})
        print(f"  → Recall@5={r['recall_at_k'].get('5', 0):.3f} MRR={r['mrr']:.3f}")
        for d in r["detail"]:
            print(
                f"      {'✅' if d['ok'] else '❌'} {d['question']} "
                f"(rank={d['first_relevant_rank']}, MRR={d['mrr']:.3f})"
            )

    print("\n═══ 对比汇总 ═══")
    best = max(results, key=lambda x: (x["recall_at_k"].get("5", 0), x["mrr"]))
    for r in results:
        mark = " 👑最优" if r["chunk_size"] == best["chunk_size"] else ""
        print(
            f"  chunk_size={r['chunk_size']:<5} "
            f"Recall@5={r['recall_at_k'].get('5', 0):.3f} "
            f"MRR={r['mrr']:.3f}{mark}"
        )
    print(f"\n建议: chunk_size={best['chunk_size']}（优先 Recall@5，其次 MRR）")


if __name__ == "__main__":
    main()
