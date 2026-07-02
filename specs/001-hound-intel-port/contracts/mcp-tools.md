# Contract: MCP ツール & 管理 API

**互換性原則**: 既存クライアント (MacBook の Claude Code) から見て破壊的変更なし。署名は code-rag 現行版を維持。

## MCP Tools (fastmcp, SSE :8765)

### search_code

```
search_code(query: str, repo: str = "default", k: int = 5,
            lang: str | None = None, path_glob: str | None = None,
            modified_since: str | None = None, author: str | None = None) -> list[dict]
```

返却 dict (Hit.to_dict()): `path, symbol, lineno_start, lineno_end, language, code, score`
**追加 (hound 取込み、非破壊)**: `suggested_ranges, context_trust="untrusted_repository_content", file_role, is_test, is_doc, is_config`

### grep_code

```
grep_code(pattern: str, repo: str = "default", path_glob: str | None = None,
          max_results: int = 30) -> list[dict]
```

返却に `context_trust` を追加 (非破壊)。

### find_references

```
find_references(symbol: str, repo: str = "default") -> list[dict]
```

grep ベース近似を維持 (AST 化は Phase 外)。

### get_file_range

```
get_file_range(path: str, start: int, end: int, repo: str = "default") -> dict
```

パラメータ名 `start`/`end` 維持 (hound の `lineno_start`/`lineno_end` には合わせない)。
hound のパストラバーサル検証・secret 検査ロジックを内部に取込み (返却構造は不変 + `context_trust`)。

### list_symbols

```
list_symbols(path: str, repo: str = "default") -> list[dict]
```

### list_repos

```
list_repos() -> list[dict]   # name, path, last_indexed_at, chunk_count
```

実装を lancedb スキャン → SQLite `repos` テーブル参照に変更 (返却キー互換)。

### stats

```
stats(days: int = 7) -> dict
```

code-rag のログ形式 (query_id ベース) 前提の集計を維持。

## HTTP Admin (custom_route)

### POST /admin/reindex

```
body: {"repo": "<name>"}  →  202 {"status": "started", "repo": "<name>"}
```

`indexer.py --repo <name>` を Popen。hooks/post-commit から呼出し。loopback bind のため LAN からは到達不可 (仕様)。

## 内部 API 契約 (モジュール間)

- `retrieval.hybrid_search(...)` — 署名・Hit dataclass 維持 (eval/run.py が `hit.to_dict()` に依存)
- `retrieval.invalidate_bm25_cache(repo: str | None = None)` — watcher / reindex が依存。vector キャッシュも同時破棄に拡張
- `chunking.chunk_file(...) -> list[Chunk]` — 無修正
- `hygiene` の filter API — 無修正
- `security.validate_server_host(host) -> list[str]` — 新規。server 起動時に呼び、違反でプロセス終了 (env `CODE_RAG_ALLOW_UNSAFE_BIND=1` でオプトイン)
- `indexer.get_db(cfg) -> sqlite3.Connection`、`indexer.index_repo(...)` — lancedb 系 API (`get_table`, `_build_row`) は廃止。watcher は新 API に追従

## 互換性チェックリスト

- [ ] eval/run.py が無修正で動く (Hit.to_dict() 互換)
- [ ] hooks/post-commit が無修正で動く (/admin/reindex 互換)
- [ ] MacBook 側 .mcp.json は URL のみ変更 (localhost トンネル経由)
