from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Quality-metadata constants (T010)
# ---------------------------------------------------------------------------
CONTEXT_TRUST = "untrusted_repository_content"
SUGGESTED_CONTEXT_WINDOW_LINES = 30
MAX_SUGGESTED_RANGE_LINES = 300


@dataclass
class Hit:
    path: str
    symbol: str
    lineno_start: int
    lineno_end: int
    language: str
    code: str
    score: float

    def to_dict(self) -> dict:
        d = {
            "path": self.path,
            "symbol": self.symbol,
            "lineno_start": self.lineno_start,
            "lineno_end": self.lineno_end,
            "language": self.language,
            "code": self.code,
            "score": self.score,
        }
        d["suggested_ranges"] = _suggested_ranges(self.lineno_start, self.lineno_end)
        d.update(_file_role_metadata(self.path, self.language))   # file_role, is_test, is_doc, is_config
        d["context_trust"] = CONTEXT_TRUST
        return d


# ---------------------------------------------------------------------------
# Quality-metadata helpers (T010) — ported from code-hound-main/retrieval.py
# ---------------------------------------------------------------------------

def _suggested_ranges(
    lineno_start: int,
    lineno_end: int,
    window: int = SUGGESTED_CONTEXT_WINDOW_LINES,
    max_lines: int = MAX_SUGGESTED_RANGE_LINES,
) -> list[dict]:
    start = max(1, lineno_start - window)
    end = max(lineno_end, lineno_end + window)
    if end - start + 1 > max_lines:
        end = start + max_lines - 1
        if end < lineno_end:
            end = lineno_end
            start = max(1, end - max_lines + 1)
    return [{
        "kind": "context_window",
        "lineno_start": start,
        "lineno_end": end,
    }]


def infer_file_role(path: str, language: str | None = None) -> str:
    normalized = path.replace("\\", "/").lower()
    parts = [part for part in normalized.split("/") if part]
    name = parts[-1] if parts else normalized
    suffix = Path(name).suffix

    if any(part in {"tests", "test", "__tests__", "spec"} for part in parts):
        return "test"
    if (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".test.js")
        or name.endswith(".test.jsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
        or name.endswith(".spec.js")
        or name.endswith(".spec.jsx")
    ):
        return "test"

    if name in {
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "makefile",
        "dockerfile",
    }:
        return "build"

    if "docs" in parts or name.startswith("readme") or suffix in {".md", ".rst", ".txt"}:
        return "docs"

    if "scripts" in parts or suffix in {".sh", ".bash", ".zsh"}:
        return "script"

    if (
        suffix in {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".conf"}
        or name.startswith("config.")
        or name.endswith(".config.js")
        or name.endswith(".config.ts")
    ):
        return "config"

    if language in {
        "python", "typescript", "tsx", "javascript", "jsx", "go", "rust",
        "ruby", "java", "kotlin", "swift", "c", "cpp", "c_sharp", "php",
        "scala", "bash", "sql",
    }:
        return "source"

    if suffix in {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java",
        ".kt", ".swift", ".c", ".cpp", ".h", ".hpp", ".cs", ".php",
        ".scala", ".sql",
    }:
        return "source"

    return "unknown"


def _file_role_metadata(path: str, language: str | None = None) -> dict:
    role = infer_file_role(path, language)
    return {
        "file_role": role,
        "is_test": role == "test",
        "is_doc": role == "docs",
        "is_config": role == "config",
    }


# ---------------------------------------------------------------------------
# Config / storage singletons
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


_config: dict | None = None
_conn: sqlite3.Connection | None = None
_embedder = None
_ranker = None
_vec_cache: dict[str, tuple[np.ndarray, list[dict]]] = {}


def _get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_conn() -> sqlite3.Connection:
    """Return the module-level SQLite connection singleton."""
    global _conn
    if _conn is None:
        cfg = _get_config()
        db_path = (ROOT / cfg["storage"]["path"]).resolve()
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        # watcher / reindex subprocesses write concurrently; wait instead of
        # failing with "database is locked".
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def _get_embedder():
    """Lazy-initialise fastembed TextEmbedding singleton."""
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding  # type: ignore
        cfg = _get_config()
        _embedder = TextEmbedding(model_name=cfg["embedding"]["model"])
    return _embedder


def _get_ranker():
    """Lazy-initialise the flashrank Ranker singleton (ONNX session load is slow)."""
    global _ranker
    if _ranker is None:
        from flashrank import Ranker
        _ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2")
    return _ranker


# ---------------------------------------------------------------------------
# Vector cache & cosine similarity (T009)
# ---------------------------------------------------------------------------

def _cosine_sim(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    q = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    normed = matrix / norms
    return normed @ q


def _load_vectors(repo: str) -> tuple[np.ndarray, list[dict]]:
    """Load all chunk vectors for *repo* from SQLite with module-level caching.

    repo == "all" → all rows, no WHERE clause.
    """
    cache_key = repo if repo != "all" else "__all__"
    if cache_key in _vec_cache:
        return _vec_cache[cache_key]

    conn = _get_conn()
    select = (
        "SELECT id, repo, path, symbol, lineno_start, lineno_end, "
        "language, code, last_modified, last_author, vector FROM chunks"
    )
    if repo != "all":
        rows = conn.execute(select + " WHERE repo=?", (repo,)).fetchall()
    else:
        rows = conn.execute(select).fetchall()

    if not rows:
        empty = np.empty((0, 384), dtype=np.float32)
        _vec_cache[cache_key] = (empty, [])
        return empty, []

    meta: list[dict] = []
    vecs: list[np.ndarray] = []
    for row in rows:
        if row["vector"]:
            vecs.append(np.frombuffer(row["vector"], dtype=np.float32))
            meta.append({
                "id": row["id"],
                "repo": row["repo"],
                "path": row["path"],
                "symbol": row["symbol"],
                "lineno_start": row["lineno_start"],
                "lineno_end": row["lineno_end"],
                "language": row["language"],
                "code": row["code"],
                "last_modified": row["last_modified"],
                "last_author": row["last_author"],
            })

    if not vecs:
        empty = np.empty((0, 384), dtype=np.float32)
        _vec_cache[cache_key] = (empty, [])
        return empty, []

    matrix = np.stack(vecs)
    _vec_cache[cache_key] = (matrix, meta)
    return matrix, meta


# ---------------------------------------------------------------------------
# BM25 via SQLite FTS5 (index lives in the DB; no in-process corpus load)
# ---------------------------------------------------------------------------

def _fts_match_query(query: str) -> str:
    """Build an FTS5 MATCH expression: each preprocessed token as a quoted
    phrase, OR-joined. Tokens without word characters would be empty phrases
    (FTS5 syntax error), so they are dropped."""
    tokens = [t for t in preprocess_query(query).split() if re.search(r"\w", t)]
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _fts_bm25_ranked(repo: str, query: str, top_k: int) -> list[tuple[str, float]]:
    """Return [(chunk_id, score)] for the BM25 top-k, best first."""
    match = _fts_match_query(query)
    if not match:
        return []
    conn = _get_conn()
    sql = "SELECT chunk_id, bm25(chunks_fts) AS r FROM chunks_fts WHERE chunks_fts MATCH ?"
    params: list = [match]
    if repo != "all":
        sql += " AND repo = ?"
        params.append(repo)
    sql += " ORDER BY r LIMIT ?"
    params.append(top_k)
    rows = conn.execute(sql, params).fetchall()
    # bm25() is smaller-is-better; negate so larger = better like rank_bm25.
    return [(row["chunk_id"], -float(row["r"])) for row in rows]


def invalidate_bm25_cache(repo: Optional[str] = None) -> None:
    """Drop the vector cache for *repo* (or all if None) after the SQLite
    table changes. BM25 now lives in FTS5 inside the DB, so only the
    in-process vector cache needs invalidating; the name is kept because
    watcher.py and server.py import it."""
    if repo is None:
        _vec_cache.clear()
    else:
        _vec_cache.pop(repo, None)
        _vec_cache.pop("__all__", None)


# ---------------------------------------------------------------------------
# Query helpers (unchanged from original)
# ---------------------------------------------------------------------------

def _parse_since(value: str) -> Optional[datetime]:
    """Accept '7d' / '24h' / 'YYYY-MM-DD' / ISO timestamps."""
    s = value.strip()
    now = datetime.now(tz=timezone.utc)
    m = re.fullmatch(r"(\d+)([dhm])", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
        return now - delta
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def preprocess_query(query: str) -> str:
    tokens = query.split()
    expanded: list[str] = []
    for tok in tokens:
        expanded.append(tok)
        parts = re.sub(r'([A-Z][a-z]+|[A-Z]+(?=[A-Z]|$))', r' \1', tok).split()
        if len(parts) > 1:
            expanded.extend(p.lower() for p in parts)
        if "_" in tok:
            expanded.extend(tok.split("_"))
    seen: set[str] = set()
    result: list[str] = []
    for t in expanded:
        t_lower = t.lower()
        if t_lower and t_lower not in seen:
            seen.add(t_lower)
            result.append(t_lower)
    return " ".join(result)


def _rrf_merge(ranked_lists: list[list[tuple[str, float]]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (doc_id, _) in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Main search entry point
# ---------------------------------------------------------------------------

def hybrid_search(
    query: str,
    repo: str,
    k: int = 5,
    lang: Optional[str] = None,
    path_glob: Optional[str] = None,
    modified_since: Optional[str] = None,
    author: Optional[str] = None,
    debug_info: Optional[dict] = None,
) -> list[Hit]:
    """Hybrid search. If *debug_info* is a dict it is populated in place with a
    per-stage scoring breakdown (vector / bm25 / rrf / rerank / final); the
    returned hits are identical either way."""
    config = _get_config()
    rcfg = config.get("retrieval", {})
    top_k_vec = rcfg.get("top_k_vector", 20)
    top_k_bm25 = rcfg.get("top_k_bm25", 20)
    rrf_k = rcfg.get("rrf_k", 60)
    use_reranker = rcfg.get("use_reranker", True)

    # --- Embed query ---
    q_vec = np.array(list(_get_embedder().embed([query]))[0], dtype=np.float32)

    # --- Load vectors ---
    matrix, meta = _load_vectors(repo)
    id_to_meta = {r["id"]: r for r in meta}

    if debug_info is not None:
        debug_info["query"] = query
        debug_info["preprocessed_query"] = preprocess_query(query)

    since_dt = _parse_since(modified_since) if modified_since else None

    def _passes_meta(r: dict) -> bool:
        if since_dt is not None:
            lm = r.get("last_modified")
            if lm is None:
                return False
            if isinstance(lm, str):
                try:
                    lm = datetime.fromisoformat(lm.replace("Z", "+00:00"))
                except ValueError:
                    return False
            if hasattr(lm, "tzinfo") and lm.tzinfo is None:
                lm = lm.replace(tzinfo=timezone.utc)
            if lm < since_dt:
                return False
        if author:
            a = r.get("last_author") or ""
            if author.lower() not in a.lower():
                return False
        return True

    # --- Vector search ---
    vec_rows: list[dict] = []
    try:
        if matrix.shape[0] > 0:
            sims = _cosine_sim(q_vec, matrix)
            top_indices = np.argsort(-sims)[:top_k_vec]
            vec_rows = [meta[i] | {"_sim": float(sims[i])} for i in top_indices]
    except Exception as e:
        vec_rows = []
        if debug_info is not None:
            debug_info["vector_error"] = str(e)

    # Apply filters
    if lang:
        vec_rows = [r for r in vec_rows if r.get("language") == lang]
    if path_glob:
        import fnmatch
        vec_rows = [r for r in vec_rows if fnmatch.fnmatch(r.get("path", ""), f"*{path_glob}*")]
    if repo != "all":
        vec_rows = [r for r in vec_rows if r.get("repo") == repo]
    if since_dt is not None or author:
        vec_rows = [r for r in vec_rows if _passes_meta(r)]

    vec_ranked = [(r["id"], float(r.get("_sim", 0.0))) for r in vec_rows]

    if debug_info is not None:
        debug_info["vector"] = [
            {"rank": i + 1, "id": r["id"], "path": r["path"], "symbol": r["symbol"],
             "cosine": round(float(r.get("_sim", 0.0)), 4)}
            for i, r in enumerate(vec_rows)
        ]

    # --- BM25 (FTS5) ---
    bm25_ranked: list[tuple[str, float]] = []
    try:
        for doc_id, score in _fts_bm25_ranked(repo, query, top_k_bm25):
            row = id_to_meta.get(doc_id)
            if row is None:
                continue
            if lang and row.get("language") != lang:
                continue
            if path_glob:
                import fnmatch
                if not fnmatch.fnmatch(row.get("path", ""), f"*{path_glob}*"):
                    continue
            if (since_dt is not None or author) and not _passes_meta(row):
                continue
            bm25_ranked.append((doc_id, score))
    except Exception as e:
        if debug_info is not None:
            debug_info["bm25_error"] = str(e)

    if debug_info is not None:
        debug_info["bm25"] = [
            {"rank": i + 1, "id": doc_id,
             "path": (id_to_meta.get(doc_id) or {}).get("path"),
             "symbol": (id_to_meta.get(doc_id) or {}).get("symbol"),
             "bm25": round(score, 4)}
            for i, (doc_id, score) in enumerate(bm25_ranked)
        ]

    # --- RRF merge ---
    merged = _rrf_merge([vec_ranked, bm25_ranked], k=rrf_k)
    top_20_ids = {doc_id for doc_id, _ in merged[:20]}

    if debug_info is not None:
        vec_rank_of = {doc_id: i + 1 for i, (doc_id, _) in enumerate(vec_ranked)}
        bm25_rank_of = {doc_id: i + 1 for i, (doc_id, _) in enumerate(bm25_ranked)}
        debug_info["rrf"] = [
            {"rank": i + 1, "id": doc_id, "rrf_score": round(s, 5),
             "vector_rank": vec_rank_of.get(doc_id),
             "bm25_rank": bm25_rank_of.get(doc_id),
             "path": (id_to_meta.get(doc_id) or {}).get("path")}
            for i, (doc_id, s) in enumerate(merged[:20])
        ]

    # Collect merged rows
    id_to_row: dict[str, dict] = {}
    for r in vec_rows:
        id_to_row[r["id"]] = r
    for doc_id, _ in bm25_ranked:
        if doc_id in top_20_ids and doc_id not in id_to_row:
            row = id_to_meta.get(doc_id)
            if row is not None:
                id_to_row[doc_id] = row

    candidates = []
    for doc_id, rrf_score in merged[:20]:
        if doc_id in id_to_row:
            r = id_to_row[doc_id]
            candidates.append((r, rrf_score))

    if not candidates:
        return []

    max_per_file = rcfg.get("max_per_file", 2)

    # --- Flashrank reranker ---
    if use_reranker and len(candidates) > 1:
        try:
            from flashrank import RerankRequest
            ranker = _get_ranker()
            passages = [{"id": i, "text": c[0]["code"], "meta": c[0]} for i, c in enumerate(candidates)]
            rerank_request = RerankRequest(query=query, passages=passages)
            results = ranker.rerank(rerank_request)
            # Tiebreak: when reranker scores cluster, prefer the original RRF order.
            ranked = sorted(
                results,
                key=lambda x: (-round(float(x["score"]), 3), int(x["id"])),
            )
            if debug_info is not None:
                debug_info["rerank"] = [
                    {"rank": i + 1, "id": item["meta"].get("id"),
                     "path": item["meta"].get("path"),
                     "symbol": item["meta"].get("symbol"),
                     "score": round(float(item["score"]), 4)}
                    for i, item in enumerate(ranked)
                ]
            # Diversify: cap chunks per file so a single file can't monopolise top-k.
            seen_per_file: dict[str, int] = {}
            picked = []
            dropped: list[dict] = []
            for item in ranked:
                p = str(item["meta"].get("path", ""))
                if seen_per_file.get(p, 0) >= max_per_file:
                    dropped.append({"id": item["meta"].get("id"), "path": p,
                                    "reason": "max_per_file"})
                    continue
                seen_per_file[p] = seen_per_file.get(p, 0) + 1
                picked.append(item)
                if len(picked) >= k:
                    break
            # Consensus guard: the RRF top-1 carries vector+BM25 agreement;
            # keep it in the final top-k even when the cross-encoder prefers
            # docstring-heavy chunks that merely mention the query terms.
            guard: dict = {"injected": False}
            top_row, top_rrf = candidates[0]
            if picked and all(
                item["meta"].get("id") != top_row.get("id") for item in picked
            ):
                if len(picked) >= k:
                    picked = picked[: k - 1]
                picked.append({"id": -1, "score": top_rrf, "meta": top_row})
                guard = {"injected": True, "id": top_row.get("id"),
                         "path": top_row.get("path")}
            if debug_info is not None:
                debug_info["diversity_dropped"] = dropped
                debug_info["consensus_guard"] = guard
            hits = []
            for item in picked:
                meta = item["meta"]
                hits.append(Hit(
                    path=str(meta.get("path", "")),
                    symbol=str(meta.get("symbol", "")),
                    lineno_start=int(meta.get("lineno_start", 0)),
                    lineno_end=int(meta.get("lineno_end", 0)),
                    language=str(meta.get("language", "")),
                    code=str(meta.get("code", "")),
                    score=float(item["score"]),
                ))
            if debug_info is not None:
                debug_info["final"] = [
                    {"rank": i + 1, "id": item["meta"].get("id"),
                     "path": item["meta"].get("path"),
                     "score": round(float(item["score"]), 4)}
                    for i, item in enumerate(picked)
                ]
            return hits
        except Exception as e:
            if debug_info is not None:
                debug_info["rerank_error"] = str(e)

    # Fallback: return RRF top-k
    hits = []
    for row, score in candidates[:k]:
        hits.append(Hit(
            path=str(row.get("path", "")),
            symbol=str(row.get("symbol", "")),
            lineno_start=int(row.get("lineno_start", 0)),
            lineno_end=int(row.get("lineno_end", 0)),
            language=str(row.get("language", "")),
            code=str(row.get("code", "")),
            score=score,
        ))
    if debug_info is not None:
        debug_info["final"] = [
            {"rank": i + 1, "id": row.get("id"), "path": row.get("path"),
             "score": round(float(score), 5), "stage": "rrf_fallback"}
            for i, (row, score) in enumerate(candidates[:k])
        ]
    return hits
