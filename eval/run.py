#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from retrieval import hybrid_search

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# 評価台 repo 名。CLAUDE.md 不変条件「引数なし `uv run python eval/run.py` で
# recall@5 ≥ 0.92」を保つため、既定は従来どおり fastapi コーパスを指す DB repo "default"。
# specs/003 で fastapi は通常運用 config.yaml の repos[] から外したが (FR-008/SC-006)、
# DB 上の索引は repo 名 "default" のまま凍結されており、eval はそれを直接クエリする
# (retrieval は repo を config で検証せず WHERE repo=? で引くため、config 除外の影響を受けない)。
# 再索引が必要な時のみ eval/config.eval.yaml の固有設定を使い chunk 数を従来同値に保つ。
EVAL_REPO = os.environ.get("CODE_RAG_EVAL_REPO", "default")


def load_qa() -> list[dict]:
    with open(EVAL_DIR / "qa.yaml") as f:
        return yaml.safe_load(f)


def hit_matches(hit: dict, expected_paths: list[str], expected_symbols: list[str]) -> bool:
    hit_path = hit.get("path", "")
    hit_symbol = hit.get("symbol", "")
    for ep in expected_paths:
        if ep in hit_path:
            return True
    for es in expected_symbols:
        if es == hit_symbol or es in hit_path:
            return True
    return False


def run_queries(qa_pairs: list[dict], verbose: bool = True) -> dict:
    total = len(qa_pairs)
    recall_hits = 0
    mrr_sum = 0.0
    details = []
    latencies_ms: list[float] = []

    if verbose:
        print(f"Evaluating {total} queries...")

    for qa in qa_pairs:
        qid = qa["id"]
        query = qa["query"]
        expected_paths = qa.get("expected_paths", [])
        expected_symbols = qa.get("expected_symbols", [])

        t0 = time.perf_counter()
        try:
            hits = hybrid_search(query=query, repo=EVAL_REPO, k=5)
        except Exception as e:
            if verbose:
                print(f"  [{qid}] ERROR: {e}")
            details.append({"id": qid, "query": query, "recall": False, "rank": None, "error": str(e)})
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        hit_dicts = [h.to_dict() for h in hits]
        matched_rank = None
        for rank, h in enumerate(hit_dicts, 1):
            if hit_matches(h, expected_paths, expected_symbols):
                matched_rank = rank
                break

        recall = matched_rank is not None
        if recall:
            recall_hits += 1
            mrr_sum += 1.0 / matched_rank

        top_paths = [h.get("path", "").split("/")[-1] for h in hit_dicts[:3]]
        status = f"rank={matched_rank}" if recall else "MISS"
        if verbose:
            print(f"  [{qid}] {status} ({elapsed_ms:.0f} ms) | top3: {top_paths}")

        details.append({
            "id": qid,
            "query": query,
            "recall": recall,
            "rank": matched_rank,
            "latency_ms": elapsed_ms,
            "top_hits": [
                {"path": h.get("path"), "symbol": h.get("symbol"), "score": h.get("score")}
                for h in hit_dicts
            ],
        })

    recall_at_5 = recall_hits / total if total > 0 else 0.0
    mrr = mrr_sum / total if total > 0 else 0.0
    avg_latency_ms = (sum(latencies_ms) / len(latencies_ms)) if latencies_ms else 0.0

    return {
        "recall_at_5": recall_at_5,
        "mrr": mrr,
        "recall_hits": recall_hits,
        "total": total,
        "avg_latency_ms": avg_latency_ms,
        "details": details,
    }


def write_results(label: str, result: dict, title: str | None = None, update_baseline: bool = False) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    full = {"phase": label, "timestamp": ts, **result}

    json_path = RESULTS_DIR / f"{label}_{ts}.json"
    with open(json_path, "w") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)

    md_path = RESULTS_DIR / f"{label}_{ts}.md"
    header = title or label
    md = f"""# {header}

**Timestamp**: {ts}
**recall@5**: {result['recall_at_5']:.2f} ({result['recall_hits']}/{result['total']})
**MRR**: {result['mrr']:.2f}
**avg latency**: {result['avg_latency_ms']:.0f} ms

## Per-Query Results

| ID | Query | Recall | Rank | Latency (ms) |
|---|---|---|---|---|
"""
    for d in result["details"]:
        recall_str = "✅" if d["recall"] else "❌"
        rank_str = str(d.get("rank") or "-")
        lat = d.get("latency_ms")
        lat_str = f"{lat:.0f}" if isinstance(lat, (int, float)) else "-"
        md += f"| {d['id']} | {d['query']} | {recall_str} | {rank_str} | {lat_str} |\n"

    with open(md_path, "w") as f:
        f.write(md)

    if update_baseline:
        baseline_path = RESULTS_DIR / "phase1_baseline.md"
        if baseline_path.exists() or baseline_path.is_symlink():
            baseline_path.unlink()
        baseline_path.symlink_to(md_path.name)

    print(f"\nResults saved:")
    print(f"  {json_path}")
    print(f"  {md_path}")
    return md_path


def evaluate(label: str = "phase1", title: str | None = None, update_baseline: bool = False) -> dict:
    qa_pairs = load_qa()
    result = run_queries(qa_pairs)

    print()
    print("=" * 40)
    print(label)
    print(f"recall@5: {result['recall_at_5']:.2f} ({result['recall_hits']}/{result['total']})")
    print(f"MRR:      {result['mrr']:.2f}")
    print(f"avg lat:  {result['avg_latency_ms']:.0f} ms")
    print("=" * 40)

    write_results(label, result, title=title, update_baseline=update_baseline)
    return result


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "phase1"
    title = sys.argv[2] if len(sys.argv) > 2 else None
    update = label == "phase1"
    evaluate(label=label, title=title, update_baseline=update)
