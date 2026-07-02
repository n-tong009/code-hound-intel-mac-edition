"""tests/test_indexer_delete.py — 差分インデックスの削除反映 (specs/003 US2 / T017).

ファイル削除後の再インデックスで、該当 chunk / chunks_fts / edges が消えること、
未変更ファイルは再 embed されないこと (差分モード) を pytest で検証 (FR-006・SC-004 / Acceptance US2-3)。
"""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

import indexer


def _config(tmp_path: Path) -> dict:
    return {
        "storage": {"path": str(tmp_path / "code_rag.db")},
        "embedding": {"provider": "fastembed", "model": "BAAI/bge-small-en-v1.5",
                      "dimension": 384, "batch_size": 8},
        "chunking": {"chunk_lines": 40, "chunk_lines_overlap": 5, "max_chars": 2000},
        "hygiene": {"respect_gitignore": False, "max_file_bytes": 102400,
                    "max_file_lines": 5000, "secret_scan": False,
                    "extra_exclude_dirs": [], "extra_exclude_globs": []},
    }


def _open(config: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(config["storage"]["path"])
    conn.row_factory = sqlite3.Row
    return conn


def _make_two_file_repo(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    a = root / "a.py"
    a.write_text(textwrap.dedent("""\
        def alpha_fn():
            return 1

        class Alpha:
            def m(self):
                return alpha_fn()
    """))
    b = root / "b.py"
    b.write_text(textwrap.dedent("""\
        from a import alpha_fn

        def beta_fn():
            return alpha_fn() + 1
    """))
    return a, b


def test_delete_removes_chunks_fts_and_edges(tmp_path):
    repo_path = tmp_path / "repo"
    a, b = _make_two_file_repo(repo_path)
    config = _config(tmp_path)
    repo_cfg = {"name": "repo", "path": str(repo_path)}

    indexer.index_repo(repo_cfg, config)

    conn = _open(config)
    b_chunks = conn.execute(
        "SELECT id FROM chunks WHERE repo=? AND path LIKE ?", ("repo", "%b.py")
    ).fetchall()
    assert b_chunks, "b.py should have chunks before deletion"
    b_ids = [r["id"] for r in b_chunks]
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE repo=? AND path LIKE ?", ("repo", "%b.py")
    ).fetchone()["n"] > 0
    conn.close()

    # Delete b.py (rsync --delete equivalent) and reindex.
    b.unlink()
    indexer.index_repo(repo_cfg, config)

    conn = _open(config)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE repo=? AND path LIKE ?", ("repo", "%b.py")
    ).fetchone()["n"] == 0, "b.py chunks must be gone"
    # chunks_fts cleared for the removed ids
    qmarks = ",".join("?" * len(b_ids))
    assert conn.execute(
        f"SELECT COUNT(*) AS n FROM chunks_fts WHERE chunk_id IN ({qmarks})", b_ids
    ).fetchone()["n"] == 0, "FTS rows for b.py must be gone"
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE repo=? AND path LIKE ?", ("repo", "%b.py")
    ).fetchone()["n"] == 0, "edges for b.py must be gone"
    # a.py survives
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE repo=? AND path LIKE ?", ("repo", "%a.py")
    ).fetchone()["n"] > 0
    conn.close()


def test_unchanged_reindex_is_stable(tmp_path):
    """変更ゼロ再インデックスで総 chunk 数が不変 (差分モード)。"""
    repo_path = tmp_path / "repo"
    _make_two_file_repo(repo_path)
    config = _config(tmp_path)
    repo_cfg = {"name": "repo", "path": str(repo_path)}

    n1 = indexer.index_repo(repo_cfg, config)
    n2 = indexer.index_repo(repo_cfg, config)
    assert n1 == n2 and n1 > 0
