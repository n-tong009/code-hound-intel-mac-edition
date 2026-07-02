# Phase 3 Final Results

**Goal**: 「毎日使える RAG サーバー」への昇格。精度は Phase 2 比 -5% 以内 (recall@5 ≥ 0.90) を保つこと。
**Result**: **recall@5 = 0.95 (19/20)**, **MRR = 0.79**, **avg latency 2974 ms** — Phase 2 と完全一致。Exit 条件突破。

## 数値表

| 施策 | recall@5 | MRR | avg latency | files | chunks |
|---|---|---|---|---|---|
| Phase 2 Final | 0.95 (19/20) | 0.79 | 2811 ms | 48 | 582 |
| Phase 3 Final | **0.95 (19/20)** | **0.79** | **2974 ms** | 48 | 582 |
| Δ | ±0 | ±0 | +5.8% | ±0 | ±0 |

詳細: `phase3_final_20260425T234626Z.md`

## 残 MISS

| ID | クエリ | 原因 |
|---|---|---|
| q12 | path parameter query parameter extraction request | Phase 2 から継続。`dependencies/utils.py` の汎用語シグナル分散。embedding 交換 (Linux 移行時) で再挑戦 |

## 運用観点メトリクス

### 1. インデックス衛生 (`hygiene.py`)
- `extra_exclude_dirs`: `docs_src` (455), `docs` (3), `scripts` (25) 除外 → 検索空間の半分以上を tutorial で埋める事故を防止
- `max_file_bytes: 262144` (256KB): fastapi の `routing.py` (197KB), `applications.py` (181KB) を含めるため。一般リポでは 100KB に下げる
- 結果: raw 531 files → kept 48 files (`drops: docs_src:455 + scripts:25 + docs:3`)
- `.gitignore` 準拠 + バイナリ判定 + 秘密情報 8 パターン (AKIA / ghp_ / sk- / xoxb / PRIVATE KEY / AIza 等)。誤混入時は `data/logs/secrets_warn.log` にパスのみ記録

### 2. インクリメンタル更新 (`watcher.py`)
- `watchdog` で監視、デバウンス 1.0 秒、`content_hash` (SHA-256) 比較で差分のみ upsert
- ファイル更新後 `retrieval.invalidate_bm25_cache(repo)` を呼んで BM25 キャッシュ無効化
- 削除/リネームは `(src) deleted + (dst) created` として処理

### 3. Git メタデータ
- `commit_sha`, `last_modified`, `last_author` (email) を chunk 毎に保持
- `search_code(query, modified_since="7d", author="alice@example.com")` でフィルタ可能

### 4. マルチリポ
- 単一テーブル `code_chunks` + `repo` カラム filter (Phase 1 構造維持)
- `list_repos()` ツールが name/path/chunk_count/last_indexed_at を返す
- `indexer.py --repo <name>` で個別 reindex (`/admin/reindex` も内部でこれを Popen)

### 5. 観測ログ (`observe.py`)
- 全 MCP ツール呼出を `data/logs/queries-YYYY-MM-DD.jsonl` に追記
- スキーマ: `ts / query_id / tool / args / returned_paths / rerank_scores / latency_ms / error`
- **コード本体は記録しない** (chunk_id とパスのみ)
- `stats(days=N)` ツール: 呼出数 / by_tool / errors / avg_latency / p95 / 頻出クエリ top10

### 6. post-commit フック
- `hooks/post-commit` (bash) → `POST /admin/reindex {repo, commit_sha}` → `indexer.py --repo <name>` を Popen で非同期起動
- `server.py` に `@mcp.custom_route("/admin/reindex", methods=["POST"])` で実装
- 失敗時も commit は成功させる (`|| true`)

### 7. 自動起動 (launchd)
- `scripts/launch_agent.plist` (server) + `scripts/launch_agent_watcher.plist` (watcher)
- `RunAtLoad + KeepAlive` で Mac mini 再起動後も自動復帰
- 登録手順は `CONNECT.md` に追記

## スキーマ拡張

```text
追加: content_hash (str), indexed_at (timestamp), file_mtime (timestamp),
      commit_sha (str), last_modified (timestamp), last_author (str)
```

lancedb 0.5.0 はカラム動的追加が制限されるため、`get_table` で旧スキーマ検出 → drop → recreate。
既存 `lance.db` は `lance.db.phase2` に退避済み。

## 構成 (最終)

`config.yaml`:
```yaml
embedding:
  provider: ollama
  base_url: http://localhost:11434
  model: nomic-embed-text
  batch_size: 128

retrieval:
  top_k_vector: 20
  top_k_bm25: 20
  top_k_final: 5
  rrf_k: 60
  use_reranker: true
  max_per_file: 2

hygiene:
  respect_gitignore: true
  max_file_bytes: 262144
  max_file_lines: 5000
  secret_scan: true
  extra_exclude_dirs: ["docs_src", "docs", "scripts"]
  extra_exclude_globs: []
```

## Exit 条件チェック

- [x] **インクリメンタル更新**: `watcher.py` 実装、デバウンス + content_hash 比較で 5 秒以内反映
- [x] **再起動後自動復帰**: launchd plist 配備、`launchctl load` 手順を `CONNECT.md` に記載
- [x] **衛生ルール検証**: `docs_src` 配下 455 files が `excluded_dir:docs_src` で除外されることを確認
- [x] **秘密情報スキャン**: `hygiene.SECRET_PATTERNS` で 8 種類の鍵パターンを検出 → ファイル全体を除外 + `secrets_warn.log` 記録
- [x] **観測ログ**: `data/logs/queries-YYYY-MM-DD.jsonl` 日次ローテ、`stats` ツール集計
- [x] **マルチリポ namespace**: `list_repos`、`repo` フィルタ、`/admin/reindex` で個別 reindex
- [x] **eval ハーネス**: recall@5 = 0.95 (Phase 2 比 ±0)、MRR = 0.79 (±0)、Exit 条件 (-5% 以内) 大幅突破
