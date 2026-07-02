from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

import indexer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> dict:
    """Build a minimal config dict with an absolute DB path."""
    db_path = tmp_path / "code_rag.db"
    return {
        "storage": {"path": str(db_path)},
        "embedding": {
            "provider": "fastembed",
            "model": "BAAI/bge-small-en-v1.5",
            "dimension": 384,
            "batch_size": 8,
        },
        "chunking": {
            "chunk_lines": 40,
            "chunk_lines_overlap": 5,
            "max_chars": 2000,
        },
        "hygiene": {
            "respect_gitignore": False,
            "max_file_bytes": 102400,
            "max_file_lines": 5000,
            "secret_scan": False,
        },
    }


def _make_mini_repo(repo_path: Path) -> tuple[Path, Path]:
    """Create two small .py files in repo_path. Return their paths."""
    repo_path.mkdir(parents=True, exist_ok=True)
    src = repo_path / "src"
    src.mkdir()

    file_a = src / "utils.py"
    file_a.write_text(
        textwrap.dedent("""\
            def add(a, b):
                return a + b


            def subtract(a, b):
                return a - b


            def multiply(a, b):
                return a * b
        """),
        encoding="utf-8",
    )

    file_b = src / "greet.py"
    file_b.write_text(
        textwrap.dedent("""\
            def hello(name):
                return f"Hello, {name}!"


            def goodbye(name):
                return f"Goodbye, {name}!"
        """),
        encoding="utf-8",
    )

    return file_a, file_b


def _open_db(config: dict) -> sqlite3.Connection:
    db_path = Path(config["storage"]["path"])
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _repo_cfg(name: str, path: Path) -> dict:
    return {"name": name, "path": str(path)}


# ---------------------------------------------------------------------------
# T019-1: index_repo returns > 0 and chunks table row count matches
# ---------------------------------------------------------------------------

def test_index_repo_returns_positive_chunk_count(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    count = indexer.index_repo(repo_cfg, config)

    assert count > 0

    conn = _open_db(config)
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE repo = ?", ("myrepo",)
    ).fetchone()
    conn.close()

    assert rows["n"] == count


# ---------------------------------------------------------------------------
# T019-2: repos table UPSERT
# ---------------------------------------------------------------------------

def test_index_repo_upserts_repos_table(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    count = indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    row = conn.execute(
        "SELECT name, path, chunk_count, last_indexed_at FROM repos WHERE name = ?",
        ("myrepo",),
    ).fetchone()
    conn.close()

    assert row is not None, "repos table should have an entry after indexing"
    assert row["name"] == "myrepo"
    assert row["path"] == str(repo_path)
    assert row["chunk_count"] == count
    assert row["last_indexed_at"] is not None


# ---------------------------------------------------------------------------
# T019-3: vector BLOB length = 384 * 4 = 1536 bytes
# ---------------------------------------------------------------------------

def test_index_repo_vector_blob_length(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    rows = conn.execute(
        "SELECT vector FROM chunks WHERE repo = ?", ("myrepo",)
    ).fetchall()
    conn.close()

    expected_bytes = 384 * 4  # float32 = 4 bytes per dimension
    for row in rows:
        blob = row["vector"]
        assert isinstance(blob, bytes), "vector column should be bytes"
        assert len(blob) == expected_bytes, (
            f"Expected {expected_bytes} bytes, got {len(blob)}"
        )


# ---------------------------------------------------------------------------
# T019-4: content_hash is non-empty
# ---------------------------------------------------------------------------

def test_index_repo_content_hash_nonempty(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    rows = conn.execute(
        "SELECT content_hash FROM chunks WHERE repo = ?", ("myrepo",)
    ).fetchall()
    conn.close()

    assert len(rows) > 0
    for row in rows:
        h = row["content_hash"]
        assert h is not None and h != "", "content_hash should be non-empty"


# ---------------------------------------------------------------------------
# T019-5: re-index does not grow the row count (DELETE → INSERT)
# ---------------------------------------------------------------------------

def test_index_repo_reindex_does_not_duplicate_chunks(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    count1 = indexer.index_repo(repo_cfg, config)
    count2 = indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE repo = ?", ("myrepo",)
    ).fetchone()
    conn.close()

    assert rows["n"] == count1, (
        f"Row count should stay at {count1} after reindex, got {rows['n']}"
    )
    assert count2 == count1, (
        f"index_repo should return same count on reindex: {count1} != {count2}"
    )


# ---------------------------------------------------------------------------
# T019-6: content change updates content_hash
# ---------------------------------------------------------------------------

def test_index_repo_content_change_updates_hash(tmp_path):
    repo_path = tmp_path / "myrepo"
    file_a, _ = _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    # First index
    indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    hashes_before = set(
        row["content_hash"]
        for row in conn.execute(
            "SELECT content_hash FROM chunks WHERE repo = ? AND path LIKE ?",
            ("myrepo", f"%{file_a.name}"),
        ).fetchall()
    )
    conn.close()

    # Modify the file
    file_a.write_text(
        textwrap.dedent("""\
            def add(a, b):
                \"\"\"Return a + b.\"\"\"
                return a + b


            def subtract(a, b):
                return a - b


            def multiply(a, b):
                result = a * b
                return result
        """),
        encoding="utf-8",
    )

    # Second index
    indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    hashes_after = set(
        row["content_hash"]
        for row in conn.execute(
            "SELECT content_hash FROM chunks WHERE repo = ? AND path LIKE ?",
            ("myrepo", f"%{file_a.name}"),
        ).fetchall()
    )
    conn.close()

    assert hashes_before != hashes_after, (
        "content_hash should change after the file is modified"
    )


# ---------------------------------------------------------------------------
# T029-1: chunks_fts stays in sync with chunks (initial index + reindex)
# ---------------------------------------------------------------------------

def test_index_repo_fts_rows_match_chunks(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    indexer.index_repo(repo_cfg, config)
    indexer.index_repo(repo_cfg, config)  # reindex must not duplicate FTS rows

    conn = _open_db(config)
    n_chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    n_fts = conn.execute("SELECT COUNT(*) AS n FROM chunks_fts").fetchone()["n"]
    conn.close()

    assert n_fts == n_chunks, f"FTS rows ({n_fts}) != chunks rows ({n_chunks})"


# ---------------------------------------------------------------------------
# T029-2: FTS5 MATCH finds an indexed token and maps back to a chunk id
# ---------------------------------------------------------------------------

def test_fts_match_returns_known_chunk(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    rows = conn.execute(
        "SELECT chunk_id, bm25(chunks_fts) AS r FROM chunks_fts "
        "WHERE chunks_fts MATCH ? AND repo = ? ORDER BY r",
        ('"subtract"', "myrepo"),
    ).fetchall()
    chunk_ids = {
        row["id"] for row in conn.execute("SELECT id FROM chunks").fetchall()
    }
    conn.close()

    assert len(rows) > 0, "FTS MATCH should find the 'subtract' token"
    for row in rows:
        assert row["chunk_id"] in chunk_ids
        assert row["r"] < 0, "bm25() should be negative (smaller = better)"


# ---------------------------------------------------------------------------
# T029-3: get_db backfills chunks_fts for a pre-FTS database
# ---------------------------------------------------------------------------

def test_get_db_backfills_fts_from_existing_chunks(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    indexer.index_repo(repo_cfg, config)

    # Simulate a pre-migration DB: drop the FTS table entirely.
    conn = _open_db(config)
    conn.execute("DROP TABLE chunks_fts")
    conn.commit()
    n_chunks = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    conn.close()

    conn2 = indexer.get_db(config)
    n_fts = conn2.execute("SELECT COUNT(*) AS n FROM chunks_fts").fetchone()["n"]
    conn2.close()

    assert n_fts == n_chunks, "get_db should backfill FTS from existing chunks"


# ---------------------------------------------------------------------------
# 002-T004: edges are rebuilt on index_repo and do not duplicate on reindex
# ---------------------------------------------------------------------------

def test_index_repo_builds_edges_without_duplicates(tmp_path):
    repo_path = tmp_path / "myrepo"
    _make_mini_repo(repo_path)

    config = _make_config(tmp_path)
    repo_cfg = _repo_cfg("myrepo", repo_path)

    indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    n1 = conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE repo=?", ("myrepo",)
    ).fetchone()["n"]
    defines = conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE repo=? AND edge_type='defines'",
        ("myrepo",),
    ).fetchone()["n"]
    conn.close()

    assert n1 > 0, "index_repo should extract graph edges"
    assert defines >= 5, "mini repo defines 5 functions (add/subtract/multiply/hello/goodbye)"

    indexer.index_repo(repo_cfg, config)

    conn = _open_db(config)
    n2 = conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE repo=?", ("myrepo",)
    ).fetchone()["n"]
    conn.close()

    assert n2 == n1, f"reindex must not duplicate edges: {n1} -> {n2}"
