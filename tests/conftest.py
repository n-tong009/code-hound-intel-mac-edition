"""tests/conftest.py — 共有フィクスチャ (specs/003 マルチ repo)。

既存テスト (test_graph / test_indexer_sqlite 等) はローカルヘルパを持つため、
ここで追加するフィクスチャは別名にして非干渉に保つ (T003)。
一時 DB + 2 repo の最小フィクスチャを提供し、repo 解決・直列化・除外テストで使う。
"""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest


def _base_config(tmp_path: Path) -> dict:
    """絶対 DB パス付きの最小 config dict。"""
    return {
        "storage": {"path": str(tmp_path / "code_rag.db")},
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
            "extra_exclude_dirs": [],
            "extra_exclude_globs": [],
        },
    }


def _write_mini_repo(repo_path: Path, marker: str) -> None:
    """言語混在の最小 repo を作成。marker でリポを区別 (検索スコープ確認用)。"""
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "mod.py").write_text(textwrap.dedent(f'''
        def {marker}_handler(x):
            """{marker} unique handler."""
            return x + 1

        class {marker.capitalize()}Service:
            def run(self):
                return {marker}_handler(0)
    '''))


@pytest.fixture
def base_config(tmp_path):
    """一時 DB を指す config dict を返す。"""
    return _base_config(tmp_path)


@pytest.fixture
def make_mini_repo(tmp_path):
    """`make_mini_repo(name)` で名前付き mini repo を作り Path を返すファクトリ。"""
    def _factory(name: str) -> Path:
        p = tmp_path / name
        _write_mini_repo(p, name)
        return p
    return _factory


@pytest.fixture
def two_repo_indexed(base_config, make_mini_repo):
    """2 つの repo (alpha / beta) を索引し (config, conn, repo_cfgs) を返す。

    各 repo は固有シンボル ({name}_handler) を持ち、スコープ漏れを検知できる。
    """
    import indexer

    repo_cfgs = {}
    for name in ("alpha", "beta"):
        path = make_mini_repo(name)
        cfg = {"name": name, "path": str(path)}
        indexer.index_repo(cfg, base_config)
        repo_cfgs[name] = cfg

    conn = sqlite3.connect(base_config["storage"]["path"])
    conn.row_factory = sqlite3.Row
    yield base_config, conn, repo_cfgs
    conn.close()
