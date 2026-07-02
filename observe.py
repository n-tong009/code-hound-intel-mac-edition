from __future__ import annotations

import json
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import security

ROOT = Path(__file__).parent
LOG_DIR = ROOT / "data" / "logs"

_lock = threading.Lock()


def _today_path() -> Path:
    security.ensure_private_dir(LOG_DIR)
    day = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"queries-{day}.jsonl"


def _scrub(value: Any) -> Any:
    """Strip code/text bodies from logged args. Keep query strings; drop chunk bodies."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "...[truncated]"
    return value


def log_call(
    tool: str,
    args: dict,
    *,
    returned_chunk_ids: Optional[list[str]] = None,
    returned_paths: Optional[list[str]] = None,
    rerank_scores: Optional[list[float]] = None,
    latency_ms: float = 0.0,
    client: str = "claude-code",
    session_id: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    qid = str(uuid.uuid4())
    record = {
        "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query_id": qid,
        "tool": tool,
        "args": _scrub(args),
        "returned_chunk_ids": returned_chunk_ids or [],
        "returned_paths": returned_paths or [],
        "rerank_scores": rerank_scores or [],
        "latency_ms": round(latency_ms, 2),
        "client": client,
        "session_id": session_id,
    }
    if error:
        record["error"] = error
    line = json.dumps(record, ensure_ascii=False)
    try:
        with _lock:
            with security.open_private_append(_today_path()) as f:
                f.write(line + "\n")
    except Exception:
        pass
    return qid


def aggregate_stats(days: int = 7) -> dict:
    security.ensure_private_dir(LOG_DIR)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    total = 0
    by_tool: Counter = Counter()
    latencies: list[float] = []
    queries: Counter = Counter()
    errors = 0
    cutoff_day = cutoff.strftime("%Y-%m-%d")
    for jl in sorted(LOG_DIR.glob("queries-*.jsonl")):
        # File names embed the UTC day; skip whole files older than the window.
        if jl.stem.removeprefix("queries-") < cutoff_day:
            continue
        try:
            with open(jl, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_str = rec.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if ts < cutoff:
                        continue
                    total += 1
                    by_tool[rec.get("tool", "unknown")] += 1
                    lat = rec.get("latency_ms")
                    if isinstance(lat, (int, float)):
                        latencies.append(float(lat))
                    if rec.get("error"):
                        errors += 1
                    args = rec.get("args") or {}
                    q = args.get("query") if isinstance(args, dict) else None
                    if isinstance(q, str):
                        queries[q] += 1
        except OSError:
            continue

    avg = sum(latencies) / len(latencies) if latencies else 0.0
    p95 = 0.0
    if latencies:
        s = sorted(latencies)
        p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
    return {
        "days": days,
        "total_calls": total,
        "by_tool": dict(by_tool),
        "errors": errors,
        "avg_latency_ms": round(avg, 1),
        "p95_latency_ms": round(p95, 1),
        "top_queries": queries.most_common(10),
    }
