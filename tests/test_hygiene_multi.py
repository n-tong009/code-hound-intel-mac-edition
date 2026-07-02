"""tests/test_hygiene_multi.py — 除外ルールが全 repo に一様適用 (specs/003 US2 / T013).

node_modules / 巨大ファイル / 秘密情報(内容ベース) / .gitignore 対象が、
どの repo でもスナップショット索引対象から外れることを検証 (FR-009)。
秘密検知は索引段 (hygiene) の責務であり rsync 段ではない点を反映。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hygiene import Hygiene, HygieneConfig, filter_files


def _cfg() -> HygieneConfig:
    # 通常運用 repo 相当: 固有除外なし・gitignore 尊重・秘密スキャン ON。
    return HygieneConfig.from_dict({
        "respect_gitignore": True,
        "max_file_bytes": 1024,
        "max_file_lines": 5000,
        "secret_scan": True,
        "extra_exclude_dirs": [],
        "extra_exclude_globs": [],
    })


def _build_repo(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text("ignored_by_git.py\n")

    good = root / "good.py"
    good.write_text("def ok():\n    return 1\n")

    nm = root / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    nm_file = nm / "index.js"
    nm_file.write_text("module.exports = 1;\n")

    big = root / "big.py"
    big.write_text("x = 1\n" * 500)  # > 1024 bytes

    secret = root / "secret.py"
    secret.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')

    gi = root / "ignored_by_git.py"
    gi.write_text("y = 2\n")

    return {"good": good, "nm": nm_file, "big": big, "secret": secret, "gi": gi}


@pytest.mark.parametrize("repo_name", ["alpha", "beta"])
def test_excludes_uniform_across_repos(tmp_path, repo_name):
    root = tmp_path / repo_name
    files = _build_repo(root)
    hyg = Hygiene(root, _cfg(), log_dir=tmp_path / "logs")

    all_files = [files["good"], files["nm"], files["big"], files["secret"], files["gi"]]
    kept, stats = filter_files(all_files, hyg)

    assert files["good"] in kept
    assert files["nm"] not in kept, "node_modules must be excluded"
    assert files["big"] not in kept, "oversized file must be excluded"
    assert files["secret"] not in kept, "secret-bearing file must be excluded (hygiene stage)"
    assert files["gi"] not in kept, ".gitignore'd file must be excluded"


def test_secret_detection_is_content_based(tmp_path):
    """rsync 段では弾けない内容ベース検知が hygiene 段で効くことを明示。"""
    root = tmp_path / "repo"
    root.mkdir()
    # ファイル名/拡張子はごく普通。中身だけに秘密 → パターン除外では無理、内容検知が必要。
    f = root / "config_loader.py"
    f.write_text('TOKEN = "AKIAIOSFODNN7EXAMPLE"  # looks ordinary by name\n')
    hyg = Hygiene(root, _cfg(), log_dir=tmp_path / "logs")
    kept, _ = filter_files([f], hyg)
    assert f not in kept
