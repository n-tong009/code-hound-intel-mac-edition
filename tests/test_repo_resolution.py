"""tests/test_repo_resolution.py — repo 解決順 (specs/003 US1 / T008).

解決順「明示引数 > 接続宣言 (env/ヘッダ) > エラー」、存在しない repo、
空文字/空白の各分岐を検証 (Acceptance 1-4 / SC-001・SC-002 / FR-002・FR-003・FR-004)。
"""
from __future__ import annotations

import pytest

import server


@pytest.fixture(autouse=True)
def fake_repos(monkeypatch):
    """config を alpha/beta の 2 repo に固定 (実 config.yaml に依存しない)。"""
    cfg = {"repos": [
        {"name": "alpha", "path": "data/snapshots/alpha", "languages": ["python"]},
        {"name": "beta", "path": "data/snapshots/beta", "languages": ["go"]},
    ]}
    monkeypatch.setattr(server, "_load_config", lambda: cfg)
    # 既定では接続宣言なし
    monkeypatch.delenv("CODE_RAG_REPO", raising=False)


def test_explicit_arg_used(monkeypatch):
    assert server._resolve_repo("alpha") == "alpha"


def test_explicit_arg_overrides_declaration(monkeypatch):
    monkeypatch.setenv("CODE_RAG_REPO", "beta")
    # 明示引数が接続宣言より優先
    assert server._resolve_repo("alpha") == "alpha"


def test_declaration_used_when_no_arg(monkeypatch):
    monkeypatch.setenv("CODE_RAG_REPO", "beta")
    assert server._resolve_repo(None) == "beta"


def test_no_arg_no_declaration_errors():
    with pytest.raises(server.RepoError) as ei:
        server._resolve_repo(None)
    payload = ei.value.payload
    assert payload["error"] == "repo_not_specified"
    assert set(payload["available_repos"]) == {"alpha", "beta"}


def test_nonexistent_repo_errors():
    with pytest.raises(server.RepoError) as ei:
        server._resolve_repo("ghost")
    payload = ei.value.payload
    assert payload["error"] == "repo_not_found"
    assert "ghost" in payload["message"]
    assert set(payload["available_repos"]) == {"alpha", "beta"}


@pytest.mark.parametrize("arg", ["", "   ", "\t"])
def test_blank_arg_falls_through_to_declaration(monkeypatch, arg):
    monkeypatch.setenv("CODE_RAG_REPO", "beta")
    assert server._resolve_repo(arg) == "beta"


@pytest.mark.parametrize("arg", ["", "   "])
def test_blank_arg_no_declaration_errors(arg):
    with pytest.raises(server.RepoError) as ei:
        server._resolve_repo(arg)
    assert ei.value.payload["error"] == "repo_not_specified"


def test_blank_declaration_treated_as_unset(monkeypatch):
    monkeypatch.setenv("CODE_RAG_REPO", "   ")
    with pytest.raises(server.RepoError) as ei:
        server._resolve_repo(None)
    assert ei.value.payload["error"] == "repo_not_specified"
