"""locking.py — repo 単位のプロセス間ロック (specs/003 T007 / FR-012)。

同一 repo への同期/インデックス並行実行による索引破損を防ぐため、
`data/locks/<repo>.lock` に対する fcntl 排他ロックで直列化する。
別 repo は別ファイルなので互いをブロックしない。

sync_repo.sh (bash) 側は同じパスを使い、Python の indexer 実行がこのロックを
保持する間は後続の index 実行が待つ。
"""
from __future__ import annotations

import errno
import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).parent
LOCK_DIR = ROOT / "data" / "locks"

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def lock_path(repo: str, lock_dir: Path | None = None) -> Path:
    """repo 名から安全なロックファイルパスを返す (パス分離記号を無害化)。"""
    safe = _SAFE.sub("_", repo) or "_"
    d = lock_dir or LOCK_DIR
    return d / f"{safe}.lock"


@contextmanager
def repo_lock(repo: str, *, blocking: bool = True, lock_dir: Path | None = None):
    """repo 単位の排他ロック。

    blocking=True: 取得まで待つ (既定。直列化)。
    blocking=False: 既にロック中なら BlockingIOError を送出 (多重起動検知用)。
    """
    d = lock_dir or LOCK_DIR
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    path = lock_path(repo, d)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        try:
            fcntl.flock(fd, flags)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise BlockingIOError(
                    f"repo '{repo}' is locked by another indexer/sync"
                ) from e
            raise
        yield path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
