"""tests/test_sync_serialize.py — repo 単位ロックの直列化 (specs/003 US2 / T012).

同一 repo への同期/インデックス二重起動がロックで直列化され、索引が破損しないこと、
別 repo は互いをブロックしないことを検証 (FR-012)。
"""
from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path

import pytest

from locking import repo_lock, lock_path


def test_lock_path_sanitizes_repo_name(tmp_path):
    # path separator must not escape the lock dir; the resolved file stays inside.
    p = lock_path("a/b/../c", lock_dir=tmp_path)
    assert p.parent == tmp_path
    assert "/" not in p.name
    assert p.resolve().parent == tmp_path.resolve()


def test_same_repo_lock_is_exclusive_nonblocking(tmp_path):
    with repo_lock("alpha", lock_dir=tmp_path):
        with pytest.raises(BlockingIOError):
            with repo_lock("alpha", blocking=False, lock_dir=tmp_path):
                pass


def test_different_repos_do_not_block(tmp_path):
    with repo_lock("alpha", lock_dir=tmp_path):
        # 別 repo は別ファイル → 取得できる
        with repo_lock("beta", blocking=False, lock_dir=tmp_path):
            pass


def _hold(lock_dir, repo, started, release_after):
    with repo_lock(repo, lock_dir=Path(lock_dir)):
        started.set()
        time.sleep(release_after)


def test_blocking_acquire_waits_for_holder(tmp_path):
    ctx = mp.get_context("spawn")
    started = ctx.Event()
    holder = ctx.Process(target=_hold, args=(str(tmp_path), "alpha", started, 0.6))
    holder.start()
    assert started.wait(timeout=5), "holder should have acquired the lock"

    t0 = time.perf_counter()
    # blocking acquire should wait until the holder releases (~0.6s)
    with repo_lock("alpha", lock_dir=tmp_path):
        waited = time.perf_counter() - t0
    holder.join(timeout=5)
    assert waited >= 0.3, f"blocking acquire returned too early ({waited:.2f}s)"
