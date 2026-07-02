#!/usr/bin/env python3
"""code-rag indexer — SQLite + fastembed edition.

Usage:
    python indexer.py [--repo <name>] [--sample <path>] [--workers N]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import yaml
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import graph
import security
from chunking import chunk_file, iter_repo_files
from hygiene import Hygiene, HygieneConfig, filter_files

console = Console()

# ---------------------------------------------------------------------------
# DDL — data-model.md が正
# ---------------------------------------------------------------------------
DDL = """
CREATE TABLE IF NOT EXISTS chunks (
    id           TEXT PRIMARY KEY,
    repo         TEXT NOT NULL,
    path         TEXT NOT NULL,
    symbol       TEXT,
    lineno_start INTEGER,
    lineno_end   INTEGER,
    language     TEXT,
    code         TEXT,
    content_hash TEXT,
    indexed_at   TEXT,
    file_mtime   TEXT,
    commit_sha   TEXT,
    last_modified TEXT,
    last_author  TEXT,
    vector       BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_repo
    ON chunks(repo);
CREATE INDEX IF NOT EXISTS idx_chunks_repo_path
    ON chunks(repo, path);

CREATE TABLE IF NOT EXISTS repos (
    name           TEXT PRIMARY KEY,
    path           TEXT NOT NULL,
    last_indexed_at TEXT,
    chunk_count    INTEGER
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    repo UNINDEXED,
    code,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS edges (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    repo      TEXT NOT NULL,
    src_kind  TEXT NOT NULL,        -- 'file' | 'symbol'
    src       TEXT NOT NULL,
    dst_kind  TEXT NOT NULL,        -- 'file' | 'symbol' | 'external'
    dst       TEXT NOT NULL,
    edge_type TEXT NOT NULL,        -- 'imports' | 'defines' | 'references'
    path      TEXT NOT NULL,        -- source file the edge was extracted from
    lineno    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_edges_repo_src ON edges(repo, src);
CREATE INDEX IF NOT EXISTS idx_edges_repo_dst ON edges(repo, dst);
CREATE INDEX IF NOT EXISTS idx_edges_repo_path ON edges(repo, path);
"""

# ---------------------------------------------------------------------------
# Embedder singleton
# ---------------------------------------------------------------------------
_embedder = None


def _get_embedder(cfg: dict):
    """Lazy-initialise fastembed TextEmbedding singleton."""
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding  # type: ignore
        model_name = cfg["embedding"]["model"]
        console.print(f"[bold]Initialising embedder ({model_name})...[/bold]")
        _embedder = TextEmbedding(model_name=model_name)
    return _embedder


def _embed_texts(texts: list[str], cfg: dict) -> list[bytes]:
    """Embed a list of texts and return each vector as a float32 BLOB."""
    embedder = _get_embedder(cfg)
    expected_dim = int(cfg["embedding"]["dimension"])
    vectors = list(embedder.embed(texts))
    blobs: list[bytes] = []
    for vec in vectors:
        arr = np.array(vec, dtype=np.float32)
        if arr.shape[0] != expected_dim:
            raise ValueError(
                f"Embedding dimension mismatch: got {arr.shape[0]}, "
                f"expected {expected_dim} (config embedding.dimension)"
            )
        blobs.append(arr.tobytes())
    return blobs


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db(cfg: dict) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply DDL.

    Resolves storage.path relative to ROOT, creates the parent directory with
    mode 0700, creates the file with mode 0600 if absent, then connects with
    check_same_thread=False and row_factory=sqlite3.Row.
    """
    raw = cfg["storage"]["path"]
    db_path = (ROOT / raw).resolve()
    security.ensure_private_dir(db_path.parent)
    security.create_private_file_if_missing(db_path)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL lets the server keep reading while watcher/reindex write; the busy
    # timeout covers writer-vs-writer overlap (watcher + reindex subprocess).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    # One-time backfill for DBs indexed before the FTS5 migration; no-op when
    # the FTS table is already populated (or the DB is empty).
    n_fts = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    if n_fts == 0:
        conn.execute(
            "INSERT INTO chunks_fts (chunk_id, repo, code) "
            "SELECT id, repo, code FROM chunks"
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Git metadata
# ---------------------------------------------------------------------------

def _git_meta(repo_root: Path, file_path: Path) -> tuple[str, Optional[str], str]:
    """Return (commit_sha, last_modified_iso, last_author) for a file."""
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        return "", None, ""
    try:
        out = subprocess.run(
            [
                "git", "-C", str(repo_root), "log", "-1",
                "--format=%H|%ae|%at", "--", str(rel),
            ],
            capture_output=True, text=True, timeout=5,
        )
        line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
        if not line:
            return "", None, ""
        sha, email, ts = line.split("|", 2)
        dt_iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        return sha, dt_iso, email
    except Exception:
        return "", None, ""


class GitMetaCache:
    """Per-repo cache of git log results keyed by absolute Path.

    Public name is preserved because watcher.py imports it as
    ``from indexer import GitMetaCache``.
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._cache: dict[Path, tuple[str, Optional[str], str]] = {}

    def get(self, path: Path) -> tuple[str, Optional[str], str]:
        if path not in self._cache:
            self._cache[path] = _git_meta(self.repo_root, path)
        return self._cache[path]


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Core indexing
# ---------------------------------------------------------------------------

def index_repo(
    repo_cfg: dict,
    config: dict,
    sample_path: Optional[str] = None,
    workers: int = 1,
) -> int:
    """Index a single repository.

    Parameters
    ----------
    repo_cfg:    One entry from config.yaml ``repos`` list.
    config:      Full loaded config dict.
    sample_path: If given, only index files whose paths start with this prefix.
    workers:     Accepted for CLI compatibility; chunking remains sequential
                 (fastembed occupies its own threads internally).

    Returns
    -------
    Number of chunks inserted.
    """
    repo_name = repo_cfg["name"]
    repo_path = Path(repo_cfg["path"]).expanduser().resolve()
    batch_size = int(config["embedding"].get("batch_size", 32))

    hcfg = HygieneConfig.from_dict(config.get("hygiene"))
    hygiene = Hygiene(repo_path, hcfg)

    # --- File enumeration ---
    raw_files = iter_repo_files(repo_path)
    if sample_path:
        scan_root = Path(sample_path).expanduser()
        raw_files = [f for f in raw_files if str(f).startswith(str(scan_root))]

    files, hyg_stats = filter_files(raw_files, hygiene)
    console.print(
        f"[cyan][indexer][/cyan] {repo_name}: {len(files)} files "
        f"(raw {len(raw_files)}); hygiene drops: {hyg_stats}"
    )

    git_cache = GitMetaCache(repo_path)
    conn = get_db(config)

    # Differential reindex (T007a / FR-006・SC-004): keep unchanged chunks (skip
    # re-embed when content_hash matches), re-embed changed/new, drop stale ids
    # (deleted files or shifted line-ranges). Edges carry no embedding cost, so
    # they are rebuilt fully (atomic delete+insert after the chunking pass, so
    # a crash or concurrent search never sees the repo with zero edges) to
    # reflect deletes. --sample keeps its old behaviour (no clear; INSERT OR
    # REPLACE the scanned subset).
    existing_hashes: dict[str, str] = {}
    if not sample_path:
        existing_hashes = {
            row["id"]: row["content_hash"]
            for row in conn.execute(
                "SELECT id, content_hash FROM chunks WHERE repo = ?", (repo_name,)
            ).fetchall()
        }
        console.print(
            f"[cyan][indexer][/cyan] differential reindex repo='{repo_name}' "
            f"(existing chunks: {len(existing_hashes)})"
        )
    else:
        console.print(
            "[yellow][indexer] WARNING: --sample does not clear existing chunks; "
            "run without --sample before relying on search results[/yellow]"
        )

    # --- Chunking + edge extraction pass ---
    all_chunks = []
    all_edges: list[tuple] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"Chunking {repo_name}", total=len(files))
        for fp in files:
            all_chunks.extend(chunk_file(fp, repo_name, config, repo_path))
            all_edges.extend(graph.extract_file_edges(fp, repo_name, repo_path))
            progress.advance(task)

    # --- Edges rebuild (code graph; --sample resyncs only the touched files) ---
    # Full run: delete + insert in one transaction so neither a crash mid-run
    # nor a concurrent search ever observes the repo with zero edges.
    if sample_path:
        if all_edges:
            conn.executemany(
                "DELETE FROM edges WHERE repo = ? AND path = ?",
                sorted({(repo_name, e[6]) for e in all_edges}),
            )
    else:
        conn.execute("DELETE FROM edges WHERE repo = ?", (repo_name,))
    if all_edges:
        conn.executemany(
            """
            INSERT INTO edges
                (repo, src_kind, src, dst_kind, dst, edge_type, path, lineno)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            all_edges,
        )
        console.print(f"[cyan][indexer][/cyan] {len(all_edges)} graph edges for '{repo_name}'")
    conn.commit()

    # Drop stale chunks: ids present before but absent now (deleted files or
    # line-range shifts). Skipped in --sample mode (partial scan).
    if not sample_path:
        current_ids = {c.id for c in all_chunks}
        stale_ids = set(existing_hashes) - current_ids
        if stale_ids:
            conn.executemany(
                "DELETE FROM chunks WHERE id = ?", [(i,) for i in stale_ids]
            )
            conn.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?", [(i,) for i in stale_ids]
            )
            conn.commit()
            console.print(
                f"[cyan][indexer][/cyan] dropped {len(stale_ids)} stale chunks "
                f"for '{repo_name}'"
            )

    # Re-embed only new/changed chunks (content_hash mismatch). Unchanged chunks
    # keep their existing rows untouched — no embedding cost (SC-004).
    to_embed = [
        c for c in all_chunks if existing_hashes.get(c.id) != c.content_hash
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    repo_chunk_count = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE repo = ?", (repo_name,)
    ).fetchone()["n"]

    if not to_embed:
        # Nothing changed (or empty repo): refresh repos meta and return.
        conn.execute(
            """
            INSERT OR REPLACE INTO repos (name, path, last_indexed_at, chunk_count)
            VALUES (?, ?, ?, ?)
            """,
            (repo_name, str(repo_path), now_iso, repo_chunk_count),
        )
        conn.commit()
        conn.close()
        console.print(
            f"[green][indexer][/green] Done. {repo_chunk_count} chunks for "
            f"'{repo_name}' (0 re-embedded)."
        )
        return repo_chunk_count

    # --- Embedding + insert pass ---
    total_inserted = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(
            f"Embedding {repo_name}", total=len(to_embed)
        )

        for i in range(0, len(to_embed), batch_size):
            batch = to_embed[i : i + batch_size]
            codes = [c.code for c in batch]
            blobs = _embed_texts(codes, config)

            rows: list[tuple] = []
            for chunk, blob in zip(batch, blobs):
                p = Path(chunk.path)
                try:
                    mtime_iso = datetime.fromtimestamp(
                        p.stat().st_mtime, tz=timezone.utc
                    ).isoformat()
                except OSError:
                    mtime_iso = now_iso

                commit_sha, last_modified, last_author = git_cache.get(p)

                rows.append((
                    chunk.id,
                    chunk.repo,
                    chunk.path,
                    chunk.symbol,
                    chunk.lineno_start,
                    chunk.lineno_end,
                    chunk.language,
                    chunk.code,
                    chunk.content_hash,
                    now_iso,
                    mtime_iso,
                    commit_sha,
                    last_modified,
                    last_author,
                    blob,
                ))

            conn.executemany(
                """
                INSERT OR REPLACE INTO chunks
                    (id, repo, path, symbol,
                     lineno_start, lineno_end, language, code,
                     content_hash, indexed_at, file_mtime,
                     commit_sha, last_modified, last_author,
                     vector)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
            # Keep FTS in sync; the delete covers --sample re-runs where
            # INSERT OR REPLACE overwrites existing chunk ids.
            conn.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
                [(c.id,) for c in batch],
            )
            conn.executemany(
                "INSERT INTO chunks_fts (chunk_id, repo, code) VALUES (?,?,?)",
                [(c.id, c.repo, c.code) for c in batch],
            )
            conn.commit()
            total_inserted += len(batch)
            progress.advance(task, len(batch))

    # --- repos UPSERT ---
    # chunk_count / return reflect the repo's *total* chunks (not just re-embedded),
    # so callers and the reindex invariant see a stable count (SC-004).
    repo_chunk_count = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE repo = ?", (repo_name,)
    ).fetchone()["n"]
    conn.execute(
        """
        INSERT OR REPLACE INTO repos (name, path, last_indexed_at, chunk_count)
        VALUES (?, ?, ?, ?)
        """,
        (repo_name, str(repo_path), now_iso, repo_chunk_count),
    )
    conn.commit()
    conn.close()

    console.print(
        f"[green][indexer][/green] Done. {repo_chunk_count} chunks for '{repo_name}' "
        f"({total_inserted} re-embedded)."
    )
    return repo_chunk_count


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="code-rag indexer (SQLite + fastembed)")
    parser.add_argument(
        "--repo", default=None,
        help="Repo name from config (default: all repos)",
    )
    parser.add_argument(
        "--sample", default=None,
        help="Only index files whose path starts with this prefix (for testing)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Accepted for compatibility; chunking is sequential",
    )
    args = parser.parse_args()

    config = load_config()
    repos: list[dict] = config.get("repos", [])

    if args.repo:
        repos = [r for r in repos if r["name"] == args.repo]
        if not repos:
            console.print(f"[red][indexer] Repo '{args.repo}' not found in config[/red]")
            sys.exit(1)

    from locking import repo_lock

    total = 0
    for repo in repos:
        console.print(
            f"[bold cyan][indexer] Indexing '{repo['name']}'[/bold cyan] → {repo['path']}"
        )
        # Serialize per-repo index runs (FR-012): a concurrent sync/index of the
        # same repo waits here instead of corrupting the index.
        with repo_lock(repo["name"]):
            n = index_repo(repo, config, sample_path=args.sample, workers=args.workers)
        total += n

    console.print(f"[bold green][indexer] All done. Total chunks: {total}[/bold green]")


if __name__ == "__main__":
    main()
