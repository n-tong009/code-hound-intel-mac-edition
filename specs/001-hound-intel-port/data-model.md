# Data Model: CodeHound Intel Edition

**Date**: 2026-06-12 | **Plan**: [plan.md](./plan.md)

## SQLite: `data/code_rag.db`

### Table: `chunks`

| カラム | 型 | 説明 |
|---|---|---|
| id | TEXT PRIMARY KEY | `sha1(repo:path:lineno_start-lineno_end)` 既存方式踏襲 |
| repo | TEXT NOT NULL | リポジトリ名 (config.yaml `repos[].name`) |
| path | TEXT NOT NULL | リポジトリルートからの相対パス |
| symbol | TEXT | AST 抽出シンボル (enclosing def/class) |
| lineno_start | INTEGER | 1-origin 開始行 |
| lineno_end | INTEGER | 終了行 |
| language | TEXT | python / typescript / ... |
| code | TEXT | AST header + コード本文 (embedding 入力と同一) |
| content_hash | TEXT | sha1(code)。watcher の差分判定キー |
| indexed_at | TEXT | ISO 8601 UTC |
| file_mtime | TEXT | ISO 8601 UTC |
| commit_sha | TEXT | インデックス時の HEAD sha (Phase 3 継承) |
| last_modified | TEXT | git log 由来の最終変更日時 (Phase 3 継承) |
| last_author | TEXT | git log 由来の最終 author (Phase 3 継承) |
| vector | BLOB | numpy float32 × 384 (bge-small-en-v1.5)。`np.frombuffer(row, dtype=np.float32)` で復元 |

インデックス: `CREATE INDEX idx_chunks_repo ON chunks(repo)`、`CREATE INDEX idx_chunks_repo_path ON chunks(repo, path)`

### Table: `repos`

| カラム | 型 | 説明 |
|---|---|---|
| name | TEXT PRIMARY KEY | リポジトリ名 |
| path | TEXT NOT NULL | 絶対パス |
| last_indexed_at | TEXT | ISO 8601 UTC |
| chunk_count | INTEGER | 集計キャッシュ (list_repos 用) |

## オンメモリ構造 (retrieval.py)

- `_vec_cache: dict[repo_key, (np.ndarray[N,384], list[meta_dict])]` — SQLite から全ロード、cosine 計算用 (hound 方式)
- `_bm25_cache: dict[repo, (BM25Okapi, list[chunk_meta])]` — 既存 code-rag 方式を維持
- 無効化: `invalidate_bm25_cache(repo)` が両キャッシュを破棄 (watcher / reindex から呼出し)

## バリデーション規則

- vector BLOB 長は必ず 384 × 4 bytes。ロード時に不一致なら該当行 skip + warning ログ
- dim は config.yaml `embedding.dimension` と照合
- code カラムは secret scan 通過済みのみ (hygiene.py が入口で保証)

## 状態遷移

```
ファイル変更 → watcher 検知 (debounce 1.0s)
  → hygiene 判定 (除外なら該当 path の chunks DELETE)
  → chunk_file() → content_hash 比較
     → 不変: skip
     → 変更: 該当 id DELETE → fastembed embed → INSERT
  → invalidate_bm25_cache(repo)
全再インデックス (indexer.py --repo X):
  → DELETE FROM chunks WHERE repo='X' → 全ファイル chunk → batch embed → INSERT
  → repos UPSERT (last_indexed_at, chunk_count)
```

## クエリログ (変更なし、JSONL)

`data/logs/queries-YYYY-MM-DD.jsonl`: ts, query_id, tool, args, returned_chunk_ids, returned_paths, rerank_scores, latency_ms, client, session_id, error — code-rag 形式を維持。書込みは security.py の private append ラッパー経由 (0600)
