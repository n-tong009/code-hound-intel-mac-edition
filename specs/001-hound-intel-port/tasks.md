# Tasks: CodeHound Intel Edition — code-rag の code-hound 良所取り進化

**Input**: Design documents from `/specs/001-hound-intel-port/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/mcp-tools.md, quickstart.md

**Tests**: Constitution IV (Test-First) により、各ストーリーでテストを実装より先に書く。検索品質は eval ゲート (T020) が担う。

**Organization**: ユーザーストーリー単位。US1 (品質維持の刷新) が MVP。

## Phase 1: Setup

- [x] T001 pyproject.toml を更新: Python `>=3.12,<3.13`、依存差替え (lancedb / llama-index / llama-index-embeddings-ollama / httpx / pandas 除去、fastembed>=0.4 / llama-index-core / uvicorn 追加、onnxruntime==1.17.3 / numpy<2.0 / flashrank / fastmcp / rank-bm25 / tree-sitter==0.21.3 / tree-sitter-languages / watchdog / pathspec / pyyaml / rich 維持)、`[tool.pytest.ini_options] testpaths=["tests"] pythonpath=["."]`、`[dependency-groups] dev=["pytest"]` — pyproject.toml
- [x] T002 .python-version を `3.12` に更新し `uv sync` で lock 再生成。`uv run python -c "import onnxruntime, numpy"` でピン互換を確認 — .python-version, uv.lock
- [x] T003 config.yaml を新構成に更新: `embedding: {provider: fastembed, model: BAAI/bge-small-en-v1.5, dimension: 384, batch_size: 32}`、`vector_db` → `storage: {type: sqlite, path: ./data/code_rag.db}`、`server.host: 127.0.0.1`。retrieval/hygiene/chunking/repos キーは現行値を維持 (use_reranker: true, max_file_bytes: 262144, extra_exclude_dirs 含む) — config.yaml

## Phase 2: Foundational (全ストーリーの前提)

- [x] T004 [P] tests/ ディレクトリ作成し、hound の tests/test_security.py を code-rag API に適応して移植 (validate_server_host の loopback 判定 / unsafe bind 拒否 / env オプトイン、private file helpers の 0600 検証。bearer token 系テストは除外)。この時点では red — tests/test_security.py
- [x] T005 security.py を hound から移植: `validate_server_host` (env 名は `CODE_RAG_ALLOW_UNSAFE_BIND`)、`ensure_private_dir`、`create_private_file_if_missing`、`write_new_private_text`、`open_private_append`、`runtime_permission_warnings`、`emit_security_warnings`。storage パス参照は config の `storage.path` に合わせる。T004 が green になること — security.py
- [x] T006 indexer.py を書換え: data-model.md の DDL (chunks: Phase 3 カラム + vector BLOB、repos テーブル、インデックス 2 本) で `get_db(cfg)` 実装。fastembed `TextEmbedding` で batch embed (`np.float32.tobytes()` で BLOB 化)、`index_repo` は `DELETE FROM chunks WHERE repo=?` → INSERT → repos UPSERT。GitMetaCache (commit_sha/last_modified/last_author) と chunk_file 呼出しは現行ロジック踏襲。CLI (`--repo`, `--sample`) 互換維持 — indexer.py
- [x] T007 [P] observe.py を微修正: ログ書込みを `security.open_private_append` 経由に変更、`data/logs` 作成を `ensure_private_dir` に。log_call 署名・JSONL スキーマ (query_id, rerank_scores 等) は不変 — observe.py

**Checkpoint**: `uv run pytest tests/test_security.py` green / `uv run python indexer.py --repo default --sample 5` で SQLite に行が入る

## Phase 3: User Story 1 - 検索品質を保ったままストレージ・embedding を刷新 (P1) 🎯 MVP

**Goal**: 同一 MCP API のまま SQLite + fastembed 化し recall@5 ≥ 0.92

**Independent Test**: `uv run python eval/run.py` で recall@5 ≥ 0.92

- [x] T008 [P] [US1] tests/test_retrieval_metadata.py を hound から適応移植: `infer_file_role` / suggested_ranges / context_trust マークの検証を code-rag の Hit.to_dict() 返却形に合わせて書く (red) — tests/test_retrieval_metadata.py
- [x] T009 [US1] retrieval.py の vector 検索層を差替え: lancedb table 参照を `_load_vectors(repo, conn)` (SQLite 全ロード + `_vec_cache`) と numpy cosine に変更。query embed は fastembed (`_get_embedder()` 遅延初期化)。`_get_bm25` の chunk ソースも SQLite に変更。`invalidate_bm25_cache(repo)` は BM25 + vector 両キャッシュ破棄に拡張。Hit dataclass / hybrid_search 署名 / RRF / flashrank reranker / diversity cap / lang・path_glob・modified_since・author フィルタは無修正維持 — retrieval.py
- [x] T010 [US1] retrieval.py に hound の品質メタデータを取込み: `infer_file_role` (file_role/is_test/is_doc/is_config)、`_suggested_ranges`、`CONTEXT_TRUST` 定数。Hit.to_dict() に追加キーとして混ぜる (既存キーは不変、contracts/mcp-tools.md 準拠)。T008 green — retrieval.py
- [x] T011 [US1] server.py を新層に接続: lancedb import / get_table 依存を除去し SQLite (indexer.get_db) ベースに。search_code / grep_code / find_references / get_file_range / list_symbols / stats のツール署名は不変。get_file_range に hound のパス検証 (リポ外トラバーサル拒否) と secret 検査を内製。grep_code / get_file_range 返却に context_trust 追加。list_repos は repos テーブル参照に変更。/admin/reindex は Popen 呼出し維持 — server.py
- [x] T012 [US1] フルインデックス実行: `uv run python indexer.py --repo default` で fastapi を新 DB に投入。chunk 数を NOTES.md 記録値 (~582、CodeSplitter/fallback 経路で変動) と照合し、大幅乖離なら chunking 経路を調査 — data/code_rag.db (生成物)
- [x] T013 [US1] eval/run.py を新実装で実行 (`--label hound_intel_port`)。recall@5 ≥ 0.92 を確認、未達なら chunk 数 → embedding → reranker の順で切り分け、達成まで T009-T012 を修正。結果ファイルをコミット対象に — eval/results/
- [x] T014 [US1] quickstart.md §8 のレイテンシ計測を実行し、現行比を記録 (SC-006) — specs/001-hound-intel-port/quickstart.md 結果追記

**Checkpoint**: recall@5 ≥ 0.92 + pytest green = MVP 完成。ここで git コミット

## Phase 4: User Story 2 - ループバック強制とトンネル経由アクセス (P2)

**Goal**: 127.0.0.1 以外で起動拒否、SSH トンネルで利用可能

**Independent Test**: host=0.0.0.0 起動拒否 / `nc -z <LAN-IP> 8765` 失敗 / トンネル経由 200

- [x] T015 [US2] server.py 起動部に `security.validate_server_host` + `runtime_permission_warnings` を組込み: 違反時は警告出力して exit 1 (`CODE_RAG_ALLOW_UNSAFE_BIND=1` でのみ通過)。T004 の該当テスト green — server.py
- [x] T016 [P] [US2] CONNECT.md を SSH トンネル方式に書換え: ~/.ssh/config 例 (Host mini)、`ssh -N -L 8765:localhost:8765 mini`、MacBook 側 .mcp.json は `http://localhost:8765/sse`、再接続手順、autossh 任意項。旧 LAN IP 直書き手順を削除 — CONNECT.md
- [x] T017 [US2] quickstart.md §5-6 を実施: 0.0.0.0 拒否確認 (exit 1)、loopback 起動 (SSE 200)、nc 非到達確認 OK (server サービスを新コードで再起動済、127.0.0.1 bind)。トンネル E2E のみ MacBook 側で要実施 — 検証のみ

**Checkpoint**: SC-004 / SC-005 達成。git コミット

## Phase 5: User Story 3 - テスト・CI 基盤 (P3)

**Goal**: pytest 安全網の完備

**Independent Test**: `uv run pytest` 全 green

- [x] T018 [P] [US3] tests/test_observe_metrics.py を hound から適応移植: code-rag の log_call 署名 (query_id 返却、rerank_scores) に合わせ、JSONL 書込み・ログファイル 0600・stats 集計を検証 — tests/test_observe_metrics.py
- [x] T019 [P] [US3] tests/test_indexer_sqlite.py を新規作成: tmp_path の小リポで index_repo → chunks/repos 行数・vector BLOB 長 (384×4 bytes)・content_hash 差分 upsert を検証 — tests/test_indexer_sqlite.py
- [x] T020 [US3] `uv run pytest` 全体 green 化 (51 passed) — tests/

**Checkpoint**: SC-003 達成。git コミット

## Phase 6: User Story 4 - RAG 専用機としての常時運用 (P3)

**Goal**: launchd 常駐 + watcher 差分インデックスの新構成切替

**Independent Test**: 対象リポ touch → 差分インデックスログ確認

- [x] T021 [US4] watcher.py を書換え: lancedb (get_table/_build_row) 依存を除去し SQLite upsert (content_hash 比較 → DELETE/INSERT) + fastembed embed に変更。debounce 1.0s / invalidate_bm25_cache 呼出し / hygiene 判定は維持 — watcher.py
- [x] T022 [P] [US4] scripts/launch_agent.plist と scripts/launch_agent_watcher.plist を確認: 両方とも installed 版と同一、変更不要 — scripts/*.plist
- [x] T023 [US4] サービス切替: server/watcher とも unload → load で新コード稼働 (server は 127.0.0.1 bind 確認済)。対象リポのファイル変更 → 差分インデックス +2 -1 → 復元 +1 -2、582 chunks 一致 — ~/Library/LaunchAgents/
- [x] T024 [US4] hooks/post-commit の動作確認: 対象リポに symlink 設置 + hook 実行 → /admin/reindex 発火 → indexer サブプロセス完走 582 chunks (reindex.log 0600) — 検証のみ

**Checkpoint**: SC-002 含む全 SC 達成

## Phase 7: Polish & Cross-Cutting

- [x] T025 [P] NOTES.md に Phase 4 (本移行) の確定事項・踏んだ罠を追記 (recall 結果、fastembed 知見、lancedb 廃止) — NOTES.md
- [x] T026 [P] README.md を整備: hound の README 構成を参考に、Intel Edition の概要・セットアップ・運用を記載 (documenter エージェント担当) — README.md
- [x] T027 reviewer + security-auditor 監査 → 採用 10 件修正 (ranker シングルトン / list_symbols パス検証 / rg "--" / symlink skip / WAL+busy_timeout / watcher lazy_init ロック / --sample 警告 / list_repos 計測 / stats ファイル名フィルタ / post-commit JSON)。修正後 eval 再計測: recall@5 0.95 維持・avg lat 2430ms (`eval/results/hound_intel_port_postreview_20260612T125246Z.*`) — 全体
- [x] T028 uv.lock 反映確認 (lancedb/ollama/pandas 除去済、httpx は fastmcp 推移依存)・旧 lance.db* 残置確認。最終コミットはユーザー実施 — 全体

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 (P1) → US2 (P2) → US3/US4 (P3) → Polish
- US2 は T005 (security.py) 完了後なら US1 と並行可能 (server.py の T011/T015 のみ直列)
- US3 の T018/T019 は US1 完了後いつでも並行可
- US4 の T021 は T006/T009 完了後。T023 は T013 (eval ゲート) 通過が絶対条件
- [P] 同士は並行可: T004/T007、T008/T016、T018/T019/T022、T025/T026

## Implementation Strategy

MVP = Phase 1-3 (US1)。recall@5 ≥ 0.92 を最速で確認し、ダメなら設計に戻る。品質確認後に US2 (security) → US3 (tests) → US4 (運用切替) を積む。サブエージェント分担: 実装=code-writer、テスト=tester、ドキュメント=documenter、レビュー=reviewer + security-auditor。eval 実行とサービス切替はオーケストレーター (Bash 必要) が直接実施。
