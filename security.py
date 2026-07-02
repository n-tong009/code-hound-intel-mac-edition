from __future__ import annotations

import ipaddress
import os
import stat
import sys
from pathlib import Path


ALLOW_UNSAFE_BIND_ENV = "CODE_RAG_ALLOW_UNSAFE_BIND"
SECURITY_BOUNDARY_NOTE = (
    "code-rag is intended for single-user local use; multi-user authorization "
    "and document-level ACLs are not implemented."
)


def _storage_path(base_dir: Path, cfg: dict) -> Path:
    raw_path = Path(cfg.get("storage", {}).get("path", "./data/code_rag.db")).expanduser()
    return raw_path.resolve() if raw_path.is_absolute() else (base_dir / raw_path).resolve()


def _host_is_loopback(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def validate_server_host(host: str, allow_unsafe_bind: bool | None = None) -> list[str]:
    if allow_unsafe_bind is None:
        allow_unsafe_bind = os.environ.get(ALLOW_UNSAFE_BIND_ENV) == "1"

    if _host_is_loopback(host):
        return []

    message = (
        f"Unsafe server.host {host!r}. Bind to 127.0.0.1 or localhost. "
        f"{SECURITY_BOUNDARY_NOTE}"
    )
    if not allow_unsafe_bind:
        raise ValueError(
            f"{message} To override intentionally, set {ALLOW_UNSAFE_BIND_ENV}=1."
        )
    return [
        f"{message} Proceeding only because {ALLOW_UNSAFE_BIND_ENV}=1 is set."
    ]


def ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def create_private_file_if_missing(path: Path) -> None:
    ensure_private_dir(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    else:
        os.close(fd)


def write_new_private_text(path: Path, text: str) -> None:
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def open_private_append(path: Path):
    create_private_file_if_missing(path)
    return path.open("a", encoding="utf-8")


def _mode_issue(path: Path, require_private: bool) -> str | None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as e:
        return f"unable to inspect permissions: {e.strerror or e.__class__.__name__}"

    group_world = mode & 0o077
    group_world_writable = mode & (stat.S_IWGRP | stat.S_IWOTH)

    if require_private and group_world:
        return f"group/world permissions present: {mode:04o}"
    if not require_private and group_world_writable:
        return f"group/world writable: {mode:04o}"
    return None


def runtime_permission_warnings(base_dir: Path, cfg: dict) -> list[str]:
    data_dir = base_dir / "data"
    logs_dir = data_dir / "logs"
    token_file = data_dir / ".bearer_token"
    db_path = _storage_path(base_dir, cfg)

    checks: list[tuple[str, Path, bool]] = [
        ("data-directory", data_dir, False),
        ("bearer-token-file", token_file, True),
        ("sqlite-database", db_path, True),
        ("logs-directory", logs_dir, False),
    ]
    if logs_dir.exists():
        checks.extend(
            ("log-file", path, True)
            for path in sorted(logs_dir.iterdir())
            if path.is_file()
        )

    warnings: list[str] = []
    for label, path, require_private in checks:
        if not path.exists():
            continue
        issue = _mode_issue(path, require_private)
        if issue:
            warnings.append(f"{label}: {path} ({issue})")
    return warnings


def emit_security_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"[code-rag] security warning: {warning}", file=sys.stderr, flush=True)
