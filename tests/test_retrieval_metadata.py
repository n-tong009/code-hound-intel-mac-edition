from __future__ import annotations

import retrieval


def test_infer_file_role_classifies_common_paths():
    cases = {
        "tests/test_security.py": "test",
        "src/auth_test.py": "test",
        "web/button.spec.ts": "test",
        "README.md": "docs",
        "docs/security.md": "docs",
        "scripts/evaluate_retrieval.py": "script",
        "config.yaml": "config",
        "settings/app.toml": "config",
        "pyproject.toml": "build",
        "uv.lock": "build",
        "src/server.py": "source",
        "assets/logo.png": "unknown",
    }

    for path, expected in cases.items():
        assert retrieval.infer_file_role(path) == expected, (
            f"infer_file_role({path!r}) expected {expected!r}, "
            f"got {retrieval.infer_file_role(path)!r}"
        )


def test_suggested_ranges_window_and_clamp():
    # 通常ウィンドウ: lineno_start=10, lineno_end=20
    # start = max(1, 10 - 30) = max(1, -20) = 1
    # end   = 20 + 30 = 50
    result = retrieval._suggested_ranges(10, 20)
    assert result == [{"kind": "context_window", "lineno_start": 1, "lineno_end": 50}]

    # max_lines クランプ: lineno_start=1, lineno_end=1000
    # 初期: start=max(1,1-30)=1, end=max(1000,1000+30)=1030 → 幅1030>300
    # クランプ後: end = start + 299 = 300 < 1000 なので end=1000
    #            start = max(1, 1000 - 299) = 701
    clamped = retrieval._suggested_ranges(1, 1000)
    assert len(clamped) == 1
    item = clamped[0]
    assert item["kind"] == "context_window"
    # 幅が max_lines (300) 以内であること
    assert item["lineno_end"] - item["lineno_start"] + 1 <= 300
    # lineno_end が元の lineno_end (1000) を下回らないこと
    assert item["lineno_end"] >= 1000


def test_hit_to_dict_includes_metadata_and_untrusted_marker():
    hit = retrieval.Hit(
        path="src/auth.py",
        symbol="validate_token",
        lineno_start=10,
        lineno_end=20,
        language="python",
        code="def validate_token(token):\n    return token",
        score=0.5,
    )
    d = hit.to_dict()

    # 既存 7 キーが元の値のまま含まれること
    assert d["path"] == "src/auth.py"
    assert d["symbol"] == "validate_token"
    assert d["lineno_start"] == 10
    assert d["lineno_end"] == 20
    assert d["language"] == "python"
    assert d["code"] == "def validate_token(token):\n    return token"
    assert d["score"] == 0.5

    # suggested_ranges: start=1, end=50 (10-30=−20→1, 20+30=50)
    assert d["suggested_ranges"] == [
        {"kind": "context_window", "lineno_start": 1, "lineno_end": 50}
    ]

    # file_role と bool フラグ
    assert d["file_role"] == "source"
    assert d["is_test"] is False
    assert d["is_doc"] is False
    assert d["is_config"] is False

    # context_trust は定数と一致
    assert d["context_trust"] == retrieval.CONTEXT_TRUST


def test_hit_to_dict_marks_test_files():
    hit = retrieval.Hit(
        path="tests/test_auth.py",
        symbol="test_login",
        lineno_start=1,
        lineno_end=10,
        language="python",
        code="def test_login(): pass",
        score=0.8,
    )
    d = hit.to_dict()

    assert d["file_role"] == "test"
    assert d["is_test"] is True
    assert d["is_doc"] is False
    assert d["is_config"] is False
