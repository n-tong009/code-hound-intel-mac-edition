from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pathspec

EXCLUDE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", "target", "vendor",
    ".idea", ".vscode", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "tests", "test",
}

EXCLUDE_EXT = {
    ".lock", ".min.js", ".min.css", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".woff", ".woff2", ".ttf",
    ".mp3", ".mp4", ".mov", ".wav",
}

EXCLUDE_FILENAMES = {
    "package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock",
    "uv.lock", "Gemfile.lock", "composer.lock", "pnpm-lock.yaml",
}

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"gho_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
]

LOG_DIR_NAME = "data/logs"
SECRETS_LOG = "secrets_warn.log"


@dataclass
class HygieneConfig:
    respect_gitignore: bool = True
    max_file_bytes: int = 102400
    max_file_lines: int = 5000
    secret_scan: bool = True
    extra_exclude_dirs: list[str] = field(default_factory=list)
    extra_exclude_globs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "HygieneConfig":
        d = d or {}
        return cls(
            respect_gitignore=bool(d.get("respect_gitignore", True)),
            max_file_bytes=int(d.get("max_file_bytes", 102400)),
            max_file_lines=int(d.get("max_file_lines", 5000)),
            secret_scan=bool(d.get("secret_scan", True)),
            extra_exclude_dirs=list(d.get("extra_exclude_dirs", []) or []),
            extra_exclude_globs=list(d.get("extra_exclude_globs", []) or []),
        )


def _load_gitignore_spec(repo_root: Path) -> Optional[pathspec.PathSpec]:
    patterns: list[str] = []
    for gi in repo_root.rglob(".gitignore"):
        try:
            rel_dir = gi.parent.relative_to(repo_root)
        except ValueError:
            continue
        try:
            text = gi.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        prefix = "" if str(rel_dir) == "." else str(rel_dir).replace("\\", "/") + "/"
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("/"):
                patterns.append(prefix + s.lstrip("/"))
            else:
                patterns.append(prefix + s)
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


class Hygiene:
    def __init__(self, repo_root: Path, cfg: HygieneConfig, log_dir: Optional[Path] = None):
        self.repo_root = repo_root
        self.cfg = cfg
        self.exclude_dirs = EXCLUDE_DIRS | set(cfg.extra_exclude_dirs)
        self.gi_spec: Optional[pathspec.PathSpec] = (
            _load_gitignore_spec(repo_root) if cfg.respect_gitignore else None
        )
        self.extra_globs = pathspec.PathSpec.from_lines("gitwildmatch", cfg.extra_exclude_globs) \
            if cfg.extra_exclude_globs else None
        self._log_dir = log_dir or (Path(__file__).parent / LOG_DIR_NAME)

    def _rel(self, path: Path) -> Optional[str]:
        try:
            return str(path.relative_to(self.repo_root)).replace("\\", "/")
        except ValueError:
            return None

    def should_skip(self, path: Path) -> tuple[bool, str]:
        """Return (skip?, reason). Lightweight checks only — no I/O."""
        parts = path.parts
        for part in parts:
            if part in self.exclude_dirs:
                return True, f"excluded_dir:{part}"

        name = path.name
        if name in EXCLUDE_FILENAMES:
            return True, f"excluded_filename:{name}"

        suffix = path.suffix.lower()
        if suffix in EXCLUDE_EXT:
            return True, f"excluded_ext:{suffix}"
        # multi-suffix like .min.js / .min.css
        for ext in EXCLUDE_EXT:
            if ext.count(".") > 1 and name.endswith(ext):
                return True, f"excluded_ext:{ext}"

        rel = self._rel(path)
        if rel is None:
            return False, ""

        if self.gi_spec and self.gi_spec.match_file(rel):
            return True, "gitignore"
        if self.extra_globs and self.extra_globs.match_file(rel):
            return True, "extra_glob"

        return False, ""

    def should_skip_content(self, path: Path) -> tuple[bool, str]:
        """Heavier checks: size, binary, secret. Caller should already have filtered should_skip()."""
        try:
            st = path.stat()
        except OSError as e:
            return True, f"stat_error:{e}"

        if st.st_size > self.cfg.max_file_bytes:
            return True, f"too_large:{st.st_size}"

        try:
            with open(path, "rb") as f:
                head = f.read(1024)
        except OSError as e:
            return True, f"read_error:{e}"

        if b"\x00" in head:
            return True, "binary"

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return True, f"read_text_error:{e}"

        line_count = text.count("\n") + 1
        if line_count > self.cfg.max_file_lines:
            return True, f"too_many_lines:{line_count}"

        if self.cfg.secret_scan and self._has_secret(text):
            self._warn_secret(path)
            return True, "secret_detected"

        return False, ""

    def _has_secret(self, text: str) -> bool:
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                return True
        return False

    def _warn_secret(self, path: Path) -> None:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_dir / SECRETS_LOG
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts} {path}\n")
        except Exception:
            pass


def filter_files(files: list[Path], hygiene: Hygiene) -> tuple[list[Path], dict[str, int]]:
    kept: list[Path] = []
    stats: dict[str, int] = {}
    for p in files:
        skip, reason = hygiene.should_skip(p)
        if skip:
            stats[reason] = stats.get(reason, 0) + 1
            continue
        skip, reason = hygiene.should_skip_content(p)
        if skip:
            stats[reason] = stats.get(reason, 0) + 1
            continue
        kept.append(p)
    return kept, stats
