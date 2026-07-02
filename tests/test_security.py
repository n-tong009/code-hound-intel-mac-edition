from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import security


# ---------------------------------------------------------------------------
# validate_server_host
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_validate_server_host_loopback_returns_empty(host):
    """Loopback addresses must never produce warnings."""
    result = security.validate_server_host(host)
    assert result == []


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.13"])
def test_validate_server_host_non_loopback_returns_warnings(host):
    """Non-loopback addresses must return a non-empty warning list."""
    result = security.validate_server_host(host, allow_unsafe_bind=True)
    assert isinstance(result, list)
    assert len(result) > 0
    assert any(host in w for w in result)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.13"])
def test_validate_server_host_allow_unsafe_bind_true_returns_warnings_not_raises(host):
    """allow_unsafe_bind=True must return warnings (not raise) for non-loopback."""
    result = security.validate_server_host(host, allow_unsafe_bind=True)
    assert len(result) > 0


def test_validate_server_host_env_var_allows_unsafe_bind(monkeypatch):
    """CODE_RAG_ALLOW_UNSAFE_BIND=1 must suppress ValueError for 0.0.0.0."""
    monkeypatch.setenv("CODE_RAG_ALLOW_UNSAFE_BIND", "1")
    result = security.validate_server_host("0.0.0.0")
    assert isinstance(result, list)
    # With the env var set the host is allowed; warnings may or may not be present
    # but the function must not raise.


def test_validate_server_host_env_var_zero_does_not_suppress(monkeypatch):
    """CODE_RAG_ALLOW_UNSAFE_BIND=0 (or absent) must NOT suppress the error."""
    monkeypatch.delenv("CODE_RAG_ALLOW_UNSAFE_BIND", raising=False)
    # Without allow_unsafe_bind=True and without the env var the implementation
    # should either raise ValueError or return a non-empty warning list.
    # We only verify it does NOT silently return [].
    try:
        result = security.validate_server_host("0.0.0.0", allow_unsafe_bind=False)
        # If it returns instead of raising it must be non-empty.
        assert len(result) > 0
    except ValueError:
        pass  # Raising is also acceptable.


# ---------------------------------------------------------------------------
# ensure_private_dir
# ---------------------------------------------------------------------------

def test_ensure_private_dir_creates_directory_with_0o700(tmp_path):
    target = tmp_path / "subdir" / "nested"
    security.ensure_private_dir(target)
    assert target.is_dir()
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o700


def test_ensure_private_dir_idempotent(tmp_path):
    target = tmp_path / "mydir"
    security.ensure_private_dir(target)
    security.ensure_private_dir(target)  # Must not raise.
    assert target.is_dir()


# ---------------------------------------------------------------------------
# create_private_file_if_missing
# ---------------------------------------------------------------------------

def test_create_private_file_if_missing_creates_file_with_0o600(tmp_path):
    file_path = tmp_path / "newdir" / "file.txt"
    security.create_private_file_if_missing(file_path)
    assert file_path.exists()
    mode = stat.S_IMODE(file_path.stat().st_mode)
    assert mode == 0o600


def test_create_private_file_if_missing_does_not_overwrite(tmp_path):
    file_path = tmp_path / "existing.txt"
    file_path.write_text("original", encoding="utf-8")
    os.chmod(file_path, 0o600)
    security.create_private_file_if_missing(file_path)
    assert file_path.read_text(encoding="utf-8") == "original"


# ---------------------------------------------------------------------------
# write_new_private_text
# ---------------------------------------------------------------------------

def test_write_new_private_text_creates_file_with_content_and_0o600(tmp_path):
    file_path = tmp_path / "config" / "settings.txt"
    security.write_new_private_text(file_path, "hello private\n")
    assert file_path.exists()
    assert file_path.read_text(encoding="utf-8") == "hello private\n"
    mode = stat.S_IMODE(file_path.stat().st_mode)
    assert mode == 0o600


def test_write_new_private_text_raises_if_file_exists(tmp_path):
    file_path = tmp_path / "existing.txt"
    file_path.write_text("already here", encoding="utf-8")
    with pytest.raises((FileExistsError, OSError)):
        security.write_new_private_text(file_path, "overwrite attempt")


# ---------------------------------------------------------------------------
# open_private_append
# ---------------------------------------------------------------------------

def test_open_private_append_creates_file_with_0o600(tmp_path):
    file_path = tmp_path / "logdir" / "app.log"
    with security.open_private_append(file_path) as fh:
        fh.write("first line\n")
    assert file_path.exists()
    mode = stat.S_IMODE(file_path.stat().st_mode)
    assert mode == 0o600


def test_open_private_append_appends_content(tmp_path):
    file_path = tmp_path / "app.log"
    with security.open_private_append(file_path) as fh:
        fh.write("line 1\n")
    with security.open_private_append(file_path) as fh:
        fh.write("line 2\n")
    content = file_path.read_text(encoding="utf-8")
    assert "line 1\n" in content
    assert "line 2\n" in content


def test_open_private_append_file_mode_preserved_after_append(tmp_path):
    file_path = tmp_path / "preserve.log"
    with security.open_private_append(file_path) as fh:
        fh.write("data\n")
    with security.open_private_append(file_path) as fh:
        fh.write("more data\n")
    mode = stat.S_IMODE(file_path.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# runtime_permission_warnings
# ---------------------------------------------------------------------------

def test_runtime_permission_warnings_returns_empty_when_no_files(tmp_path):
    cfg = {"storage": {"path": str(tmp_path / "code_rag.db")}}
    result = security.runtime_permission_warnings(tmp_path, cfg)
    assert result == []


def test_runtime_permission_warnings_warns_on_overpermissioned_db(tmp_path):
    db_path = tmp_path / "data" / "code_rag.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"sqlite placeholder")
    os.chmod(db_path, 0o777)  # Intentionally overpermissioned.

    cfg = {"storage": {"path": str(db_path)}}
    warnings = security.runtime_permission_warnings(tmp_path, cfg)

    assert len(warnings) > 0
    assert any(str(db_path) in w for w in warnings)


def test_runtime_permission_warnings_uses_default_storage_path(tmp_path):
    """With empty cfg, default path ./data/code_rag.db should be resolved."""
    default_db = tmp_path / "data" / "code_rag.db"
    default_db.parent.mkdir(parents=True)
    default_db.write_bytes(b"sqlite placeholder")
    os.chmod(default_db, 0o777)

    cfg = {}
    warnings = security.runtime_permission_warnings(tmp_path, cfg)

    # The default path must be inspected and the warning must reference the db file.
    assert any("code_rag.db" in w for w in warnings)


def test_runtime_permission_warnings_warns_on_overpermissioned_log_file(tmp_path):
    logs_dir = tmp_path / "data" / "logs"
    logs_dir.mkdir(parents=True)
    log_file = logs_dir / "queries-2026-06-01.jsonl"
    log_file.write_text("{}\n", encoding="utf-8")
    os.chmod(log_file, 0o644)  # group-readable → not private.

    cfg = {"storage": {"path": str(tmp_path / "data" / "code_rag.db")}}
    warnings = security.runtime_permission_warnings(tmp_path, cfg)

    assert any(str(log_file) in w for w in warnings)


def test_runtime_permission_warnings_cfg_storage_path_key(tmp_path):
    """storage.path in cfg must be respected over the default."""
    custom_db = tmp_path / "custom" / "mydb.db"
    custom_db.parent.mkdir(parents=True)
    custom_db.write_bytes(b"sqlite placeholder")
    os.chmod(custom_db, 0o777)

    cfg = {"storage": {"path": str(custom_db)}}
    warnings = security.runtime_permission_warnings(tmp_path, cfg)

    assert any(str(custom_db) in w for w in warnings)


# ---------------------------------------------------------------------------
# emit_security_warnings
# ---------------------------------------------------------------------------

def test_emit_security_warnings_writes_to_stderr(capsys):
    security.emit_security_warnings(["danger: something exposed"])
    captured = capsys.readouterr()
    assert "danger: something exposed" in captured.err


def test_emit_security_warnings_empty_list_produces_no_output(capsys):
    security.emit_security_warnings([])
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
