#!/usr/bin/env python3
"""Watch configured repos and incrementally update the SQLite index.

Run: uv run watcher.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import graph
from chunking import chunk_file
from hygiene import Hygiene, HygieneConfig
from indexer import GitMetaCache, _embed_texts, get_db

DEBOUNCE_SECONDS = 1.0


def _load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


class RepoWatcher(FileSystemEventHandler):
    def __init__(self, repo_name: str, repo_path: Path, config: dict):
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self.config = config
        self.hygiene = Hygiene(repo_path, HygieneConfig.from_dict(config.get("hygiene")))
        self.git_cache = GitMetaCache(repo_path)
        self._pending: dict[Path, str] = {}
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._apply_lock = threading.Lock()
        self._lazy_init_done = False
        self._conn: Optional[sqlite3.Connection] = None

    def _lazy_init(self):
        # _apply_lock guards against two debounce timer threads initialising
        # concurrently (flag alone is not atomic across threads).
        with self._apply_lock:
            if self._lazy_init_done:
                return
            self._conn = get_db(self.config)
            # warm up the fastembed singleton (managed in indexer)
            _embed_texts(["hello"], self.config)
            self._lazy_init_done = True

    def _enqueue(self, path: Path, kind: str):
        with self._lock:
            existing = self._pending.get(path)
            # delete trumps modify; create after delete should re-index.
            if kind == "deleted":
                self._pending[path] = "deleted"
            elif existing == "deleted" and kind in ("created", "modified"):
                self._pending[path] = "modified"
            else:
                self._pending.setdefault(path, kind)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self):
        with self._lock:
            pending = self._pending
            self._pending = {}
            self._timer = None
        if not pending:
            return
        try:
            self._lazy_init()
        except Exception as e:
            print(f"[watcher] lazy init failed: {e}", file=sys.stderr)
            return

        try:
            self._apply(pending)
        except Exception as e:
            print(f"[watcher] flush error: {e}", file=sys.stderr)

    def _apply(self, pending: dict[Path, str]):
        from retrieval import invalidate_bm25_cache
        with self._apply_lock:
            changed = False
            now_iso = datetime.now(timezone.utc).isoformat()
            for path, kind in pending.items():
                if kind == "deleted":
                    if self._delete_path(path):
                        changed = True
                    continue
                # created / modified: only files inside the watched repo
                # (watchdog events can surface symlinked/moved paths).
                try:
                    path.resolve().relative_to(self.repo_path.resolve())
                except (ValueError, OSError):
                    continue
                # ignore if hygiene says skip
                skip, _ = self.hygiene.should_skip(path)
                if skip:
                    continue
                if not path.exists():
                    # raced with delete
                    if self._delete_path(path):
                        changed = True
                    continue
                skip, _ = self.hygiene.should_skip_content(path)
                if skip:
                    continue
                chunks = chunk_file(path, self.repo_name, self.config, self.repo_path)
                if not chunks:
                    continue
                existing = self._existing_chunks_for_path(str(path))
                existing_by_id = {r["id"]: r["content_hash"] for r in existing}
                new_ids = {c.id for c in chunks}
                to_drop = [cid for cid in existing_by_id.keys() if cid not in new_ids]
                to_upsert = [c for c in chunks if existing_by_id.get(c.id) != c.content_hash]
                if to_drop:
                    self._conn.executemany(
                        "DELETE FROM chunks WHERE id=?",
                        [(cid,) for cid in to_drop],
                    )
                    self._conn.executemany(
                        "DELETE FROM chunks_fts WHERE chunk_id=?",
                        [(cid,) for cid in to_drop],
                    )
                    changed = True
                if to_upsert:
                    # invalidate git cache so a fresh commit_sha gets picked up
                    self.git_cache._cache.pop(path, None)
                    blobs = _embed_texts([c.code for c in to_upsert], self.config)
                    rows: list[tuple] = []
                    for chunk, blob in zip(to_upsert, blobs):
                        p = Path(chunk.path)
                        try:
                            mtime_iso = datetime.fromtimestamp(
                                p.stat().st_mtime, tz=timezone.utc
                            ).isoformat()
                        except OSError:
                            mtime_iso = now_iso
                        commit_sha, last_modified, last_author = self.git_cache.get(p)
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
                    self._conn.executemany(
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
                    # Keep FTS in sync (REPLACE above may overwrite existing ids).
                    self._conn.executemany(
                        "DELETE FROM chunks_fts WHERE chunk_id=?",
                        [(c.id,) for c in to_upsert],
                    )
                    self._conn.executemany(
                        "INSERT INTO chunks_fts (chunk_id, repo, code) VALUES (?,?,?)",
                        [(c.id, c.repo, c.code) for c in to_upsert],
                    )
                    changed = True
                if to_drop or to_upsert:
                    # Resync code-graph edges in the same transaction as the
                    # chunk update (FR-006); unchanged content skips the
                    # re-extract entirely.
                    self._conn.execute(
                        "DELETE FROM edges WHERE repo=? AND path=?",
                        (self.repo_name, str(path)),
                    )
                    edge_rows = graph.extract_file_edges(path, self.repo_name, self.repo_path)
                    if edge_rows:
                        self._conn.executemany(
                            """
                            INSERT INTO edges
                                (repo, src_kind, src, dst_kind, dst, edge_type, path, lineno)
                            VALUES (?,?,?,?,?,?,?,?)
                            """,
                            edge_rows,
                        )
                    self._conn.commit()
                print(f"[watcher] {kind} {path}: +{len(to_upsert)} -{len(to_drop)}", flush=True)
            if changed:
                self._conn.execute(
                    "UPDATE repos SET chunk_count = (SELECT COUNT(*) FROM chunks WHERE repo=?), "
                    "last_indexed_at=? WHERE name=?",
                    (self.repo_name, now_iso, self.repo_name),
                )
                self._conn.commit()
                invalidate_bm25_cache(self.repo_name)

    def _existing_chunks_for_path(self, path_str: str) -> list[dict]:
        try:
            cur = self._conn.execute(
                "SELECT id, content_hash FROM chunks WHERE repo=? AND path=?",
                (self.repo_name, path_str),
            )
            return [{"id": row["id"], "content_hash": row["content_hash"]} for row in cur.fetchall()]
        except Exception:
            return []

    def _delete_path(self, path: Path) -> bool:
        try:
            # FTS first: the subselect needs the chunks rows to still exist.
            self._conn.execute(
                "DELETE FROM chunks_fts WHERE chunk_id IN "
                "(SELECT id FROM chunks WHERE repo=? AND path=?)",
                (self.repo_name, str(path)),
            )
            cur = self._conn.execute(
                "DELETE FROM chunks WHERE repo=? AND path=?",
                (self.repo_name, str(path)),
            )
            self._conn.execute(
                "DELETE FROM edges WHERE repo=? AND path=?",
                (self.repo_name, str(path)),
            )
            self._conn.commit()
            if cur.rowcount > 0:
                print(f"[watcher] deleted {cur.rowcount} chunks for {path}", flush=True)
                return True
            return False
        except Exception as e:
            print(f"[watcher] delete failed for {path}: {e}", file=sys.stderr)
            return False

    # ── watchdog callbacks ──
    def on_created(self, event: FileSystemEvent):
        if event.is_directory:
            return
        self._enqueue(Path(event.src_path), "created")

    def on_modified(self, event: FileSystemEvent):
        if event.is_directory:
            return
        self._enqueue(Path(event.src_path), "modified")

    def on_deleted(self, event: FileSystemEvent):
        if event.is_directory:
            return
        self._enqueue(Path(event.src_path), "deleted")

    def on_moved(self, event: FileSystemEvent):
        if event.is_directory:
            return
        src = Path(event.src_path)
        dst = Path(event.dest_path)
        self._enqueue(src, "deleted")
        self._enqueue(dst, "created")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None,
                        help="Watch only this repo by name (default: all repos)")
    args = parser.parse_args()

    config = _load_config()
    repos = config.get("repos", [])
    if args.repo:
        repos = [r for r in repos if r["name"] == args.repo]
        if not repos:
            print(f"[watcher] repo '{args.repo}' not in config", file=sys.stderr)
            sys.exit(1)

    observer = Observer()
    handlers = []
    for r in repos:
        path = Path(r["path"])
        if not path.exists():
            print(f"[watcher] skip missing path: {path}", file=sys.stderr)
            continue
        h = RepoWatcher(r["name"], path, config)
        observer.schedule(h, str(path), recursive=True)
        handlers.append(h)
        print(f"[watcher] watching {r['name']} at {path}")

    if not handlers:
        print("[watcher] nothing to watch", file=sys.stderr)
        sys.exit(1)

    observer.start()
    print(f"[watcher] started at {datetime.now(tz=timezone.utc).isoformat()}", flush=True)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("[watcher] stopping")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
