#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import yaml
from fastmcp import FastMCP

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import security
from hygiene import SECRET_PATTERNS
from retrieval import CONTEXT_TRUST, _get_conn, hybrid_search, invalidate_bm25_cache
from observe import log_call, aggregate_stats

mcp = FastMCP("code-rag")

_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_FILE_RANGE_LINES = 300


def _load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _resolve_repo_path(repo: str) -> Optional[Path]:
    config = _load_config()
    for r in config.get("repos", []):
        if r["name"] == repo:
            return Path(r["path"])
    return None


# ---------------------------------------------------------------------------
# repo 解決 (specs/003 T004-T006 / FR-002・FR-003・FR-004)
# 解決順: 明示引数 > 接続宣言 (SSE=X-Repo ヘッダ / stdio=CODE_RAG_REPO env) > エラー。
# 暗黙 "default" フォールバックは廃止。接続経路は T006a PoC で確定 (header 方式)。
# ---------------------------------------------------------------------------

class RepoError(Exception):
    """repo 未指定/不在を構造化ペイロードで返すための内部例外。"""

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(payload.get("message", ""))


def _available_repos() -> list[str]:
    return [r["name"] for r in _load_config().get("repos", [])]


def _repo_error(kind: str, repo: str = "") -> dict:
    messages = {
        "repo_not_specified": (
            "repo を指定してください。接続で X-Repo ヘッダ "
            "(stdio は CODE_RAG_REPO env) を宣言するか、repo 引数を渡してください。"
        ),
        "repo_not_found": f"repo '{repo}' は存在しません。",
    }
    return {
        "error": kind,
        "message": messages[kind],
        "available_repos": _available_repos(),
    }


def _connection_repo() -> Optional[str]:
    """接続宣言された repo を返す。SSE=X-Repo ヘッダ優先、無ければ stdio env。"""
    try:
        from fastmcp.server.dependencies import get_http_headers
        headers = get_http_headers() or {}
        val = headers.get("x-repo")
        if val and val.strip():
            return val.strip()
    except Exception:
        pass
    import os
    val = os.environ.get("CODE_RAG_REPO")
    if val and val.strip():
        return val.strip()
    return None


def _resolve_repo(arg_repo: Optional[str]) -> str:
    """解決済み repo 名を返す。未指定/不在は RepoError を送出。"""
    if arg_repo and arg_repo.strip():
        repo = arg_repo.strip()
    else:
        repo = _connection_repo()
    if not repo:
        raise RepoError(_repo_error("repo_not_specified"))
    if repo not in _available_repos():
        raise RepoError(_repo_error("repo_not_found", repo))
    return repo


def _allowed_roots() -> list[Path]:
    config = _load_config()
    return [Path(r["path"]).expanduser().resolve() for r in config.get("repos", [])]


def _validate_path(path: Path) -> Optional[Path]:
    try:
        p = path.expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    for root in _allowed_roots():
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    return None


def _scan_secrets(text: str) -> Optional[str]:
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return pat.pattern[:20]
    return None


def _validate_line_range(start: int, end: int) -> Optional[str]:
    if start < 1:
        return "invalid line range: start must be >= 1"
    if end < start:
        return "invalid line range: end must be >= start"
    if end - start + 1 > _MAX_FILE_RANGE_LINES:
        return f"requested line range too large (max {_MAX_FILE_RANGE_LINES} lines)"
    return None


@mcp.tool()
def search_code(
    query: str,
    repo: Optional[str] = None,
    k: int = 5,
    lang: Optional[str] = None,
    path_glob: Optional[str] = None,
    modified_since: Optional[str] = None,
    author: Optional[str] = None,
) -> list[dict]:
    """Hybrid (vector + BM25 + rerank) search. Optional filters: language, path glob,
    `modified_since` (e.g. '7d', '24h', or 'YYYY-MM-DD'), `author` (substring on email)."""
    t0 = time.perf_counter()
    err: Optional[str] = None
    hits = []
    try:
        repo = _resolve_repo(repo)
    except RepoError as e:
        log_call(tool="search_code", args={"query": query, "repo": repo, "k": k},
                 latency_ms=(time.perf_counter() - t0) * 1000.0, error=e.payload["error"])
        return [e.payload]
    try:
        hits = hybrid_search(
            query=query, repo=repo, k=k, lang=lang, path_glob=path_glob,
            modified_since=modified_since, author=author,
        )
    except Exception as e:
        err = str(e)
        raise
    finally:
        elapsed = (time.perf_counter() - t0) * 1000.0
        log_call(
            tool="search_code",
            args={"query": query, "repo": repo, "k": k, "lang": lang,
                  "path_glob": path_glob, "modified_since": modified_since,
                  "author": author},
            returned_paths=[f"{h.path}:{h.lineno_start}-{h.lineno_end}" for h in hits],
            rerank_scores=[h.score for h in hits],
            latency_ms=elapsed,
            error=err,
        )
    return [h.to_dict() for h in hits]


@mcp.tool()
def search_code_debug(
    query: str,
    repo: Optional[str] = None,
    k: int = 5,
    lang: Optional[str] = None,
    path_glob: Optional[str] = None,
    modified_since: Optional[str] = None,
    author: Optional[str] = None,
) -> dict:
    """Same ranking as search_code, but also returns the per-stage scoring
    breakdown (vector / bm25 / rrf / rerank / diversity_dropped /
    consensus_guard / final) for diagnosing why a chunk did or didn't rank."""
    t0 = time.perf_counter()
    err: Optional[str] = None
    hits = []
    debug: dict = {}
    try:
        repo = _resolve_repo(repo)
    except RepoError as e:
        log_call(tool="search_code_debug", args={"query": query, "repo": repo, "k": k},
                 latency_ms=(time.perf_counter() - t0) * 1000.0, error=e.payload["error"])
        return {"hits": [], "debug": {}, "error": e.payload, "context_trust": CONTEXT_TRUST}
    try:
        hits = hybrid_search(
            query=query, repo=repo, k=k, lang=lang, path_glob=path_glob,
            modified_since=modified_since, author=author, debug_info=debug,
        )
    except Exception as e:
        err = str(e)
        raise
    finally:
        elapsed = (time.perf_counter() - t0) * 1000.0
        log_call(
            tool="search_code_debug",
            args={"query": query, "repo": repo, "k": k, "lang": lang,
                  "path_glob": path_glob, "modified_since": modified_since,
                  "author": author},
            returned_paths=[f"{h.path}:{h.lineno_start}-{h.lineno_end}" for h in hits],
            rerank_scores=[h.score for h in hits],
            latency_ms=elapsed,
            error=err,
        )
    return {
        "hits": [h.to_dict() for h in hits],
        "debug": debug,
        "context_trust": CONTEXT_TRUST,
    }


@mcp.tool()
def grep_code(
    pattern: str,
    repo: Optional[str] = None,
    path_glob: Optional[str] = None,
    max_results: int = 30,
) -> list[dict]:
    """Exact / regex match. Returns path + lineno + matched_line for each hit."""
    t0 = time.perf_counter()
    try:
        repo = _resolve_repo(repo)
    except RepoError as e:
        log_call(tool="grep_code",
                 args={"pattern": pattern, "repo": repo, "path_glob": path_glob,
                       "max_results": max_results},
                 latency_ms=(time.perf_counter() - t0) * 1000.0,
                 error=e.payload["error"])
        return [e.payload]
    repo_path = _resolve_repo_path(repo)

    results: list[dict] = []
    err: Optional[str] = None
    try:
        rg_args = ["rg", "--line-number", "--no-heading", "--color=never"]
        if path_glob:
            rg_args += ["--glob", path_glob]
        # "--" keeps a leading-dash pattern from being parsed as an rg flag.
        rg_args += ["--", pattern, str(repo_path)]
        proc = subprocess.run(rg_args, capture_output=True, text=True, timeout=15)
        for line in proc.stdout.splitlines():
            if len(results) >= max_results:
                break
            parts = line.split(":", 2)
            if len(parts) >= 3:
                results.append({
                    "path": parts[0],
                    "lineno": int(parts[1]),
                    "line": parts[2],
                })
    except FileNotFoundError:
        # Python re fallback
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            err = f"Invalid regex: {e}"
            results = [{"error": err}]
        else:
            from chunking import iter_repo_files
            for fpath in iter_repo_files(repo_path):
                if path_glob and not fnmatch.fnmatch(str(fpath), f"*{path_glob}*"):
                    continue
                try:
                    for lineno, line in enumerate(fpath.read_text(errors="ignore").splitlines(), 1):
                        if compiled.search(line):
                            results.append({"path": str(fpath), "lineno": lineno, "line": line.strip()})
                            if len(results) >= max_results:
                                break
                    if len(results) >= max_results:
                        break
                except Exception:
                    continue
    except Exception as e:
        err = str(e)

    log_call(
        tool="grep_code",
        args={"pattern": pattern, "repo": repo, "path_glob": path_glob,
              "max_results": max_results},
        returned_paths=[r.get("path", "") for r in results if "path" in r],
        latency_ms=(time.perf_counter() - t0) * 1000.0,
        error=err,
    )
    return [{**r, "context_trust": CONTEXT_TRUST} for r in results]


@mcp.tool()
def get_file_range(path: str, start: int, end: int, repo: Optional[str] = None) -> dict:
    """Return lines start..end (1-indexed, inclusive) from a file in the repo."""
    t0 = time.perf_counter()
    try:
        repo = _resolve_repo(repo)
    except RepoError as e:
        log_call(tool="get_file_range",
                 args={"path": path, "start": start, "end": end, "repo": repo},
                 latency_ms=(time.perf_counter() - t0) * 1000.0, error=e.payload["error"])
        return {"content": "", "error": e.payload, "context_trust": CONTEXT_TRUST}
    repo_path = _resolve_repo_path(repo)
    target = Path(path)
    if not target.is_absolute() and repo_path:
        target = repo_path / path

    err: Optional[str] = None
    out: str = ""
    range_error = _validate_line_range(start, end)
    validated = _validate_path(target)
    if range_error:
        out = f"Access denied: {range_error}"
        err = "invalid_range"
    elif validated is None:
        out = f"Access denied: path outside configured repos: {path}"
        err = "path_outside_repos"
    elif not validated.exists():
        out = f"File not found: {path}"
        err = "not_found"
    elif validated.stat().st_size > _MAX_FILE_BYTES:
        out = f"Access denied: file too large (max {_MAX_FILE_BYTES // 1_048_576}MB)"
        err = "file_too_large"
    else:
        try:
            text = validated.read_text(errors="ignore")
            hit = _scan_secrets(text)
            if hit:
                out = f"Access denied: secret pattern detected ({hit})"
                err = "secret_detected"
            else:
                lines = text.splitlines()
                out = "\n".join(lines[max(0, start - 1):end])
        except Exception as e:
            out = f"Error reading file: {e}"
            err = str(e)

    log_call(
        tool="get_file_range",
        args={"path": path, "start": start, "end": end, "repo": repo},
        latency_ms=(time.perf_counter() - t0) * 1000.0,
        error=err,
    )
    return {"content": out, "error": err, "context_trust": CONTEXT_TRUST}


@mcp.tool()
def find_references(symbol: str, repo: Optional[str] = None) -> list[dict]:
    """Find definitions and references of a symbol via the code graph
    (kind: definition | reference). Falls back to grep approximation
    (approximate: true) when the graph has no entry for the symbol."""
    t0 = time.perf_counter()
    import graph
    try:
        repo = _resolve_repo(repo)
    except RepoError as e:
        log_call(tool="find_references", args={"symbol": symbol, "repo": repo},
                 latency_ms=(time.perf_counter() - t0) * 1000.0, error=e.payload["error"])
        return [e.payload]
    err: Optional[str] = None
    refs: list[dict] = []
    try:
        refs = graph.find_refs(_get_conn(), repo, symbol)
    except Exception as e:
        err = str(e)
    if refs:
        results = [
            {**r, "approximate": False, "context_trust": CONTEXT_TRUST}
            for r in refs
        ]
        log_call(
            tool="find_references",
            args={"symbol": symbol, "repo": repo, "fallback": False},
            returned_paths=[r["path"] for r in refs],
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            error=err,
        )
        return results

    # Graph miss (or graph not built yet): grep approximation.
    pattern = rf"\b{re.escape(symbol)}\b"
    grep_results = grep_code(pattern=pattern, repo=repo, max_results=50)
    results = [
        {**r, "kind": "reference", "approximate": True} for r in grep_results
    ]
    log_call(
        tool="find_references",
        args={"symbol": symbol, "repo": repo, "fallback": True,
              "fallback_reason": "graph_error" if err else "graph_miss"},
        returned_paths=[r.get("path", "") for r in results if "path" in r],
        latency_ms=(time.perf_counter() - t0) * 1000.0,
        error=err,
    )
    return results


@mcp.tool()
def related_code(
    target: str,
    repo: Optional[str] = None,
    depth: int = 1,
    max_results: int = 50,
) -> dict:
    """Expand the code-relation neighbourhood of a symbol or file via BFS over
    the code graph. Returns related files/symbols with direction-aware
    edge_type (imports/imported_by, defines/defined_in, references/
    referenced_by). depth is clamped to 1..3, max_results to 1..50."""
    t0 = time.perf_counter()
    import graph
    try:
        repo = _resolve_repo(repo)
    except RepoError as e:
        log_call(tool="related_code",
                 args={"target": target, "repo": repo, "depth": depth,
                       "max_results": max_results},
                 latency_ms=(time.perf_counter() - t0) * 1000.0, error=e.payload["error"])
        return {"target": {"kind": None, "value": target}, "depth": depth,
                "related": [], "truncated": False, "error": e.payload,
                "context_trust": CONTEXT_TRUST}
    err: Optional[str] = None
    repo_path = _resolve_repo_path(repo)

    # Path-looking targets: resolve relative to the repo and validate.
    # Anything containing a separator or a leading dot is a path, never a
    # symbol — a failed path lookup must not fall through to symbol search.
    looks_like_path = "/" in target or target.startswith(".")
    candidate = Path(target)
    if not candidate.is_absolute() and repo_path:
        candidate = repo_path / target
    validated = _validate_path(candidate)

    early_error: Optional[str] = None
    if validated is not None and validated.exists():
        query_target = str(validated)
    elif looks_like_path or Path(target).is_absolute():
        early_error = "path_outside_repos" if validated is None else "target_not_found"
    elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", target):
        early_error = "invalid_target"
    else:
        query_target = target  # symbol name

    if early_error:
        out = {
            "target": {"kind": "file" if looks_like_path else None, "value": target},
            "depth": depth,
            "related": [],
            "truncated": False,
            "error": early_error,
            "context_trust": CONTEXT_TRUST,
        }
        log_call(
            tool="related_code",
            args={"target": target, "repo": repo, "depth": depth,
                  "max_results": max_results},
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            error=early_error,
        )
        return out

    try:
        out = graph.bfs_expand(
            _get_conn(), repo, query_target, depth=depth, max_results=max_results
        )
    except Exception as e:
        err = str(e)
        out = {
            "target": {"kind": None, "value": target},
            "depth": depth,
            "related": [],
            "truncated": False,
            "error": err,
        }
    out["context_trust"] = CONTEXT_TRUST
    log_call(
        tool="related_code",
        args={"target": target, "repo": repo, "depth": depth,
              "max_results": max_results},
        returned_paths=[
            str(r.get("value", "")) for r in out.get("related", [])
            if r.get("kind") == "file"
        ],
        latency_ms=(time.perf_counter() - t0) * 1000.0,
        error=err or out.get("error"),
    )
    return out


@mcp.tool()
def list_symbols(path: str, repo: Optional[str] = None) -> list[dict]:
    """List all function/class definitions in a file using tree-sitter."""
    t0 = time.perf_counter()
    try:
        repo = _resolve_repo(repo)
    except RepoError as e:
        log_call(tool="list_symbols", args={"path": path, "repo": repo},
                 latency_ms=(time.perf_counter() - t0) * 1000.0, error=e.payload["error"])
        return [e.payload]
    repo_path = _resolve_repo_path(repo)
    target = Path(path)
    if not target.is_absolute() and repo_path:
        target = repo_path / path

    err: Optional[str] = None
    result: list[dict]
    validated = _validate_path(target)
    if validated is None:
        result = [{"error": f"Access denied: path outside configured repos: {path}"}]
        err = "path_outside_repos"
    elif not validated.exists():
        result = [{"error": f"File not found: {path}"}]
        err = "not_found"
    else:
        target = validated
        from chunking import _detect_language, _extract_symbols
        language = _detect_language(target)
        if language is None:
            result = [{"error": f"Unsupported language for: {path}"}]
            err = "unsupported_lang"
        else:
            try:
                source = target.read_text(errors="ignore")
                symbols = _extract_symbols(source, language)
                result = [
                    {"name": name, "lineno_start": start, "lineno_end": end}
                    for name, start, end in symbols
                ]
            except Exception as e:
                result = [{"error": str(e)}]
                err = str(e)

    log_call(
        tool="list_symbols",
        args={"path": path, "repo": repo},
        latency_ms=(time.perf_counter() - t0) * 1000.0,
        error=err,
    )
    return result


@mcp.tool()
def list_repos() -> list[dict]:
    """Return configured repos with chunk count and latest indexed_at per repo."""
    t0 = time.perf_counter()
    config = _load_config()
    repos = config.get("repos", [])
    indexed: dict[str, dict] = {}
    try:
        rows = _get_conn().execute(
            "SELECT name, path, last_indexed_at, chunk_count FROM repos"
        ).fetchall()
        indexed = {row["name"]: dict(row) for row in rows}
    except Exception:
        indexed = {}

    out: list[dict] = []
    for r in repos:
        info = {
            "name": r["name"],
            "path": r["path"],
            "languages": r.get("languages", []),
            "chunk_count": 0,
        }
        row = indexed.get(r["name"])
        if row:
            info["chunk_count"] = int(row.get("chunk_count") or 0)
            if row.get("last_indexed_at"):
                info["last_indexed_at"] = str(row["last_indexed_at"])
        out.append(info)
    log_call(tool="list_repos", args={},
             latency_ms=(time.perf_counter() - t0) * 1000.0)
    return out


@mcp.tool()
def stats(days: int = 7) -> dict:
    """Aggregate query log stats over the last N days."""
    t0 = time.perf_counter()
    s = aggregate_stats(days=days)
    log_call(tool="stats", args={"days": days},
             latency_ms=(time.perf_counter() - t0) * 1000.0)
    return s


@mcp.custom_route("/admin/reindex", methods=["POST"])
async def admin_reindex(request):
    """Trigger a re-index for one repo, called by post-commit hook."""
    from starlette.responses import JSONResponse
    try:
        body = await request.json()
    except Exception:
        body = {}
    repo = body.get("repo")
    commit_sha = body.get("commit_sha")
    if not repo:
        return JSONResponse({"ok": False, "error": "missing repo"}, status_code=400)

    config = _load_config()
    repos = [r for r in config.get("repos", []) if r["name"] == repo]
    if not repos:
        return JSONResponse({"ok": False, "error": f"repo '{repo}' not in config"}, status_code=404)

    # Run indexer as a subprocess so the running server isn't blocked by import-time state.
    log_path = ROOT / "data" / "logs" / "reindex.log"
    security.ensure_private_dir(log_path.parent)
    security.create_private_file_if_missing(log_path)
    cmd = [sys.executable, str(ROOT / "indexer.py"), "--repo", repo]
    try:
        with open(log_path, "ab") as lf:
            proc = subprocess.Popen(cmd, stdout=lf, stderr=lf, cwd=str(ROOT))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    invalidate_bm25_cache(repo)
    log_call(
        tool="admin_reindex",
        args={"repo": repo, "commit_sha": commit_sha, "pid": proc.pid},
        latency_ms=0.0,
    )
    return JSONResponse({"ok": True, "repo": repo, "pid": proc.pid})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="sse", choices=["stdio", "sse"])
    args = parser.parse_args()

    if args.transport == "sse":
        config = _load_config()
        srv = config.get("server", {})
        host = srv.get("host", "127.0.0.1")
        try:
            warnings = security.validate_server_host(host)
        except ValueError as e:
            print(f"[code-rag] refusing to start: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
        warnings += security.runtime_permission_warnings(ROOT, config)
        security.emit_security_warnings(warnings)
        mcp.run(
            transport="sse",
            host=host,
            port=srv.get("port", 8765),
        )
    else:
        mcp.run()
