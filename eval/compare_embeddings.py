#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import retrieval
from eval.run import load_qa, run_queries
from indexer import index_repo, load_config as load_indexer_config

RESULTS_DIR = ROOT / "eval" / "results"


def alias(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def _reset_retrieval_state(config: dict) -> None:
    retrieval._config = config
    retrieval._table = None
    retrieval._embed_model = None
    retrieval._bm25_index = None


def _ensure_indexed(base_config: dict, model: str, db_path: str, sample_path: str | None = None) -> None:
    db_dir = ROOT / db_path
    if db_dir.exists() and any(db_dir.iterdir()):
        print(f"  [skip index] {db_dir} exists")
        return

    print(f"  [index] {model} -> {db_dir} (sample={sample_path})")
    cfg = copy.deepcopy(base_config)
    cfg["embedding"]["model"] = model
    cfg["vector_db"]["path"] = db_path
    for repo_cfg in cfg.get("repos", []):
        index_repo(repo_cfg, cfg, sample_path=sample_path)


def compare(models: list[str], sample_path: str | None = None) -> list[dict]:
    base_config = load_indexer_config()
    qa_pairs = load_qa()
    results: list[dict] = []

    for model in models:
        print(f"\n=== Model: {model} ===")
        a = alias(model)
        db_path = f"./data/lance.db.{a}"

        _ensure_indexed(base_config, model, db_path, sample_path=sample_path)

        eval_config = copy.deepcopy(base_config)
        eval_config["embedding"]["model"] = model
        eval_config["vector_db"]["path"] = db_path
        _reset_retrieval_state(eval_config)

        r = run_queries(qa_pairs)
        print(f"  recall@5={r['recall_at_5']:.2f} MRR={r['mrr']:.2f} lat={r['avg_latency_ms']:.0f}ms")
        results.append({
            "model": model,
            "alias": a,
            "db_path": db_path,
            **r,
        })

    return results


def write_summary(results: list[dict]) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    md_path = RESULTS_DIR / f"phase2_embeddings_{ts}.md"

    baseline = results[0] if results else None
    md = f"""# Phase 2 - Embedding A/B Comparison

**Timestamp**: {ts}
**Baseline model**: {baseline['model'] if baseline else '-'}

| model | recall@5 | MRR | avg latency (ms) | Δ recall vs baseline |
|---|---|---|---|---|
"""
    for r in results:
        delta = r["recall_at_5"] - (baseline["recall_at_5"] if baseline else 0.0)
        md += f"| {r['model']} | {r['recall_at_5']:.2f} ({r['recall_hits']}/{r['total']}) | {r['mrr']:.2f} | {r['avg_latency_ms']:.0f} | {delta:+.2f} |\n"

    md += "\n## Per-Query Ranks\n\n| ID | Query |"
    for r in results:
        md += f" {r['alias']} |"
    md += "\n|---|---|"
    for _ in results:
        md += "---|"
    md += "\n"
    total = results[0]["total"] if results else 0
    for i in range(total):
        qid = results[0]["details"][i]["id"]
        query = results[0]["details"][i]["query"]
        row = f"| {qid} | {query} |"
        for r in results:
            d = r["details"][i]
            row += f" {'✅' if d['recall'] else '❌'} r={d.get('rank') or '-'} |"
        md += row + "\n"

    with open(md_path, "w") as f:
        f.write(md)

    latest = RESULTS_DIR / "phase2_embeddings.md"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(md_path.name)

    print(f"\nWrote {md_path}")
    print(f"     {latest} -> {md_path.name}")
    return md_path


if __name__ == "__main__":
    args = sys.argv[1:]
    sample = None
    models = []
    i = 0
    while i < len(args):
        if args[i] == "--sample" and i + 1 < len(args):
            sample = args[i + 1]
            i += 2
        else:
            models.append(args[i])
            i += 1
    if not models:
        models = ["nomic-embed-text", "bge-m3"]
    results = compare(models, sample_path=sample)
    write_summary(results)
