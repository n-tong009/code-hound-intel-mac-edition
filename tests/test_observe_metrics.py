from __future__ import annotations

import json
import re
import stat
import uuid
from datetime import datetime, timezone

import pytest

import observe


# ---------------------------------------------------------------------------
# log_call: basic contract
# ---------------------------------------------------------------------------

def test_log_call_returns_uuid_string(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")
    qid = observe.log_call("search_code", {"query": "auth"})
    try:
        val = uuid.UUID(qid, version=4)
    except ValueError:
        pytest.fail(f"log_call returned non-UUID value: {qid!r}")
    assert str(val) == qid


def test_log_call_writes_jsonl_record(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    qid = observe.log_call(
        "search_code",
        {"query": "parse tokens"},
        returned_chunk_ids=["c1", "c2"],
        returned_paths=["src/auth.py"],
        rerank_scores=[0.9, 0.8],
        latency_ms=42.5,
        client="test-client",
        session_id="sess-abc",
    )

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = log_dir / f"queries-{today}.jsonl"
    assert log_file.exists(), "JSONL file should be created"

    lines = [l for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1

    rec = json.loads(lines[0])

    # Required keys
    assert rec["query_id"] == qid
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", rec["ts"])
    assert rec["tool"] == "search_code"
    assert rec["args"] == {"query": "parse tokens"}
    assert rec["returned_chunk_ids"] == ["c1", "c2"]
    assert rec["returned_paths"] == ["src/auth.py"]
    assert rec["rerank_scores"] == [0.9, 0.8]
    assert rec["latency_ms"] == 42.5
    assert rec["client"] == "test-client"
    assert rec["session_id"] == "sess-abc"


def test_log_call_defaults_are_present(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")

    observe.log_call("list_repos", {})

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = (tmp_path / "logs") / f"queries-{today}.jsonl"
    rec = json.loads(log_file.read_text(encoding="utf-8").strip())

    assert rec["returned_chunk_ids"] == []
    assert rec["returned_paths"] == []
    assert rec["rerank_scores"] == []
    assert rec["latency_ms"] == 0.0
    assert rec["client"] == "claude-code"
    assert rec["session_id"] is None


# ---------------------------------------------------------------------------
# log_call: error field
# ---------------------------------------------------------------------------

def test_log_call_error_key_present_when_specified(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")
    observe.log_call("search_code", {"query": "x"}, error="index not ready")

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = (tmp_path / "logs") / f"queries-{today}.jsonl"
    rec = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert "error" in rec
    assert rec["error"] == "index not ready"


def test_log_call_error_key_absent_when_no_error(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")
    observe.log_call("search_code", {"query": "x"})

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = (tmp_path / "logs") / f"queries-{today}.jsonl"
    rec = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert "error" not in rec


# ---------------------------------------------------------------------------
# log_call: truncation of long strings in args
# ---------------------------------------------------------------------------

def test_log_call_truncates_long_args_strings(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")
    long_val = "x" * 600
    observe.log_call("search_code", {"query": long_val})

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = (tmp_path / "logs") / f"queries-{today}.jsonl"
    rec = json.loads(log_file.read_text(encoding="utf-8").strip())

    stored = rec["args"]["query"]
    assert len(stored) <= 514, f"Expected truncated value, got length {len(stored)}"
    assert stored.endswith("...[truncated]")
    assert stored[:500] == long_val[:500]


def test_log_call_does_not_truncate_short_strings(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")
    short_val = "x" * 499
    observe.log_call("search_code", {"query": short_val})

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = (tmp_path / "logs") / f"queries-{today}.jsonl"
    rec = json.loads(log_file.read_text(encoding="utf-8").strip())

    assert rec["args"]["query"] == short_val


def test_log_call_truncates_nested_args_strings(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")
    long_val = "z" * 501
    observe.log_call("search_code", {"nested": {"deep": long_val}})

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = (tmp_path / "logs") / f"queries-{today}.jsonl"
    rec = json.loads(log_file.read_text(encoding="utf-8").strip())

    stored = rec["args"]["nested"]["deep"]
    assert stored.endswith("...[truncated]")


# ---------------------------------------------------------------------------
# log_call: file permissions
# ---------------------------------------------------------------------------

def test_log_call_file_permission_is_0600(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "LOG_DIR", tmp_path / "logs")
    observe.log_call("search_code", {"query": "perm test"})

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = (tmp_path / "logs") / f"queries-{today}.jsonl"
    assert log_file.exists()
    mode = stat.S_IMODE(log_file.stat().st_mode)
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# aggregate_stats
# ---------------------------------------------------------------------------

def _write_log(log_dir, entries: list[dict]) -> None:
    """Write a list of record dicts to today's JSONL file."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"queries-{today}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def test_aggregate_stats_total_calls(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    # Write 3 calls directly so we bypass log_call's UTC file naming
    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    ts = f"{today_iso}T10:00:00Z"
    _write_log(log_dir, [
        {"ts": ts, "query_id": "q1", "tool": "search_code", "args": {"query": "foo"},
         "returned_chunk_ids": [], "returned_paths": [], "rerank_scores": [],
         "latency_ms": 10.0, "client": "claude-code", "session_id": None},
        {"ts": ts, "query_id": "q2", "tool": "search_code", "args": {"query": "bar"},
         "returned_chunk_ids": [], "returned_paths": [], "rerank_scores": [],
         "latency_ms": 20.0, "client": "claude-code", "session_id": None},
        {"ts": ts, "query_id": "q3", "tool": "list_repos", "args": {},
         "returned_chunk_ids": [], "returned_paths": [], "rerank_scores": [],
         "latency_ms": 5.0, "client": "claude-code", "session_id": None},
    ])

    stats = observe.aggregate_stats(days=7)

    assert stats["total_calls"] == 3
    assert stats["days"] == 7


def test_aggregate_stats_by_tool(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    ts = f"{today_iso}T10:00:00Z"
    _write_log(log_dir, [
        {"ts": ts, "query_id": "q1", "tool": "search_code", "args": {},
         "latency_ms": 10.0},
        {"ts": ts, "query_id": "q2", "tool": "search_code", "args": {},
         "latency_ms": 10.0},
        {"ts": ts, "query_id": "q3", "tool": "list_repos", "args": {},
         "latency_ms": 5.0},
    ])

    stats = observe.aggregate_stats(days=7)

    assert stats["by_tool"]["search_code"] == 2
    assert stats["by_tool"]["list_repos"] == 1


def test_aggregate_stats_errors(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    ts = f"{today_iso}T10:00:00Z"
    _write_log(log_dir, [
        {"ts": ts, "query_id": "q1", "tool": "search_code", "args": {},
         "latency_ms": 10.0, "error": "index not ready"},
        {"ts": ts, "query_id": "q2", "tool": "search_code", "args": {},
         "latency_ms": 20.0},
        {"ts": ts, "query_id": "q3", "tool": "search_code", "args": {},
         "latency_ms": 30.0, "error": "timeout"},
    ])

    stats = observe.aggregate_stats(days=7)

    assert stats["errors"] == 2


def test_aggregate_stats_avg_latency_ms(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    ts = f"{today_iso}T10:00:00Z"
    _write_log(log_dir, [
        {"ts": ts, "query_id": "q1", "tool": "search_code", "args": {},
         "latency_ms": 10.0},
        {"ts": ts, "query_id": "q2", "tool": "search_code", "args": {},
         "latency_ms": 30.0},
    ])

    stats = observe.aggregate_stats(days=7)

    assert stats["avg_latency_ms"] == 20.0


def test_aggregate_stats_top_queries(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    ts = f"{today_iso}T10:00:00Z"
    _write_log(log_dir, [
        {"ts": ts, "query_id": "q1", "tool": "search_code",
         "args": {"query": "parse tokens"}, "latency_ms": 10.0},
        {"ts": ts, "query_id": "q2", "tool": "search_code",
         "args": {"query": "parse tokens"}, "latency_ms": 10.0},
        {"ts": ts, "query_id": "q3", "tool": "search_code",
         "args": {"query": "auth flow"}, "latency_ms": 10.0},
    ])

    stats = observe.aggregate_stats(days=7)

    top = dict(stats["top_queries"])
    assert top.get("parse tokens") == 2
    assert top.get("auth flow") == 1


def test_aggregate_stats_empty_log_dir(monkeypatch, tmp_path):
    log_dir = tmp_path / "empty_logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    stats = observe.aggregate_stats(days=7)

    assert stats["total_calls"] == 0
    assert stats["errors"] == 0
    assert stats["avg_latency_ms"] == 0.0
    assert stats["by_tool"] == {}
    assert stats["top_queries"] == []


def test_aggregate_stats_excludes_old_entries(monkeypatch, tmp_path):
    """Entries older than the cutoff window must not be counted."""
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(observe, "LOG_DIR", log_dir)

    # Write one old entry into a dated file that still gets scanned
    old_ts = "2000-01-01T00:00:00Z"
    today_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"queries-{today_iso}.jsonl"
    log_file.write_text(
        json.dumps({"ts": old_ts, "query_id": "old", "tool": "search_code",
                    "args": {}, "latency_ms": 5.0}) + "\n",
        encoding="utf-8",
    )

    stats = observe.aggregate_stats(days=1)

    assert stats["total_calls"] == 0
