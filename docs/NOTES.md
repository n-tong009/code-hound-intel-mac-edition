# 開発ノート (Phase 1–6)

> **読み方**: これは Phase 1 → 6 の時系列開発ログ。**最新の確定状態は [README.md](README.md) と本ファイル最下部の Phase 6 を参照**。初期フェーズ (Phase 1–3) の記述の一部は後続フェーズで置き換わっている。置き換わった箇所には `🔁 superseded by Phase N` を添えた。歴史として残すため本文は削っていない。

## 環境の制約（Intel Mac 固有）

- **LanceDB**: v0.30+ は Intel Mac (macOS x86_64) 非対応。`0.5.0` にピン固定。
  - FTS インデックス (`create_fts_index`) の API は 0.5.0 では制限あり。BM25 は `rank_bm25` で代替。
  - > 🔁 superseded by Phase 4–5: ストレージは SQLite 単一ファイル (`data/code_rag.db`) へ、BM25 は SQLite FTS5 へ移行。LanceDB・`rank_bm25` 依存は `pyproject.toml` から除去済 (onnxruntime / numpy のピンは現役)。
- **onnxruntime**: 1.18+ は Intel Mac 非対応。`1.17.3` にピン固定。
  - NumPy も `<2.0` (1.26.4) にピン固定が必要。
- **numpy**: `<2.0` に制約（onnxruntime 1.17.3 との互換性）。

## 既知の懸念点

- Phase 1 時点での `find_references` は grep ベース近似。完全な AST ベース実装は Phase 4 予定。
  - > 🔁 superseded by Phase 6: コードグラフ (`edges` テーブル) 照会へ昇格。グラフ未登録シンボルのみ grep フォールバック (`approximate: true`)。
- BM25 インデックスはサーバー起動時にメモリへ全ロード。大規模リポジトリでは起動が遅くなる可能性あり。
  - > 🔁 superseded by Phase 5: SQLite FTS5 へ移行し、起動時の全 chunk RAM ロードは廃止 (インデックスは DB 内に永続)。
- サーバー機の DHCP IP が変わった場合、~/.ssh/config の HostName（= <server-ip>）を更新すること。

## Phase 2 以降の改善候補

> 🔁 多くは Phase 3–6 で実現済: 差分インデックス更新 = Phase 3、BM25 のオンメモリ脱却 = FTS5 移行 (Phase 5)、`find_references` の tree-sitter 化 = Phase 6、launchd 自動起動 = Phase 3。LanceDB の Linux 移行のみ未着手 (Intel ピンを外す前提のため保留)。

- LanceDB を Linux サーバーやコンテナに移行すれば最新版が使える（FTS、再帰フィルタ等が強化）
- BM25 をオンメモリからファイルベースに（大規模対応）
- `find_references` を tree-sitter の参照クエリで実装
- ファイル監視による差分インデックス更新（`watchdog` 導入済み）
- launchd での自動起動（Phase 3 予定）

## Phase 2 確定事項

- **recall@5 0.80 → 0.95** (+18.75% 相対) で Exit 条件 (≥ 0.92) クリア
- 効いた施策: AST header (+10pp) + diversity cap (+5pp)
- 効かなかった施策: query preprocessing 単独（後段の AST/diversity と組み合わせて初めて効く）
- 中止した施策: bge-m3 への embedding 交換 — Intel Mac CPU では 1 batch も終わらず時間枠で断念。Linux/CUDA 移行時に再挑戦すべき

## Phase 3 (Ops Hygiene) で必要なログ基盤

- 各クエリの (timestamp, query, top-k hits, latency) を JSONL で永続化
- 失敗クエリ（recall MISS）の自動収集 → 評価 QA への追加候補
- インデックス再構築の所要時間トラッキング
- Ollama embed call のレイテンシ分布

## bge-m3 を再挑戦するための前提

- Linux + GPU（CUDA）環境への移行が必要
- `eval/compare_embeddings.py` は完成済 → `--sample` 付きで実行できる
- `pyproject.toml` の onnxruntime/lancedb のピンも Linux なら緩められる

## 評価対象リポジトリ

| 名前 | パス |
|---|---|
| default | `~/code-rag-targets/fastapi` |

## Phase 3 確定事項 (recall@5=0.95 維持、Phase 2 と完全一致)

- **スキーマ追加**: `content_hash`, `indexed_at`, `file_mtime`, `commit_sha`, `last_modified`, `last_author`
  - lancedb 0.5.0 はカラム動的追加が安定しないため **テーブル drop → 再作成** で対応（`indexer.get_table` が判定）
  - 既存 `lance.db` は `lance.db.phase2` に退避済み
- **インクリメンタル更新**: `watcher.py` が watchdog で監視。`DEBOUNCE_SECONDS=1.0`、ファイル毎に既存 chunk と `content_hash` 比較し差分のみ upsert。BM25 は `retrieval.invalidate_bm25_cache(repo)` で無効化
- **衛生**: `hygiene.py` が `.gitignore` + ハードコード除外 + サイズ/行数/バイナリ/秘密情報スキャン。秘密検出時は `data/logs/secrets_warn.log` に**パスのみ**記録
- **観測**: 全 MCP ツール呼出を `data/logs/queries-YYYY-MM-DD.jsonl` に記録（コード本体は記録しない、パスのみ）。`stats(days=N)` ツールで集計
- **マルチリポ**: 単一テーブル `code_chunks` + `repo` フィルタを継続。`list_repos` 追加
- **post-commit 補完**: `hooks/post-commit` → `POST /admin/reindex` → `indexer.py --repo <name>` を Popen
- **launchd**: server / watcher 各 plist。nohup プロセスを kill してから load

## Phase 3 で踏んだ罠 (再発防止メモ)

- **`max_file_bytes` 100KB はデフォルト過小**: fastapi の `routing.py` (197KB) と `applications.py` (181KB) が `too_large` で除外され recall 0.85 まで落ちた。最重要モジュールは大きい傾向があるので、デフォルト 256KB に拡張済 (`config.yaml`)。
- **docs_src/scripts/docs を除外しないと検索空間爆発**: fastapi リポは `docs_src/` に 455 files の tutorial を持つ。Phase 2 の SKIP_DIRS では除外されない。これらを取り込むと recall@5 が 0.95 → 0.70 に劣化する。`hygiene.extra_exclude_dirs` で対応。一般リポでは個別判断。
- **indexer の重複 add**: 同じ id で `table.add` を繰り返すと重複 chunks が増える。`index_repo` 冒頭で `table.delete(\"repo = '<name>'\")` を呼んでから add する設計に修正済 (sample 指定時は除く)。
- **lancedb 0.5.0 のスキーマ進化制限**: カラム動的追加は不安定。`get_table` で `content_hash in schema.names` を判定し、旧スキーマなら drop → recreate。
- **chunking.py の chunk 数に注意**: CodeSplitter 経路 vs fallback 経路で chunk 数が大きく変わる。Phase 2 の 582 chunks は fallback (40-line block) ベース。挙動が変わったら eval 比較で気付ける。

## Phase 4 確定事項 (CodeHound Intel Edition — SQLite + fastembed 移行)

- **recall@5 = 0.95 (19/20) 維持**。MRR 0.84、avg lat 2700ms (旧 2974ms)。miss は q12 のみで旧ベースラインと同一 (`eval/results/hound_intel_port_20260612T103723Z.*`)
- レビュー指摘修正後 (flashrank Ranker シングルトン化など) の再計測: recall@5 0.95 維持、avg lat **2430ms** (`eval/results/hound_intel_port_postreview_20260612T125246Z.*`)
- **lancedb 0.5.0 廃止 → SQLite 単一ファイル** (`data/code_rag.db`)。vector は float32 BLOB (384×4=1536 bytes)、cosine は numpy オンメモリ (`_vec_cache`)。582 chunks 規模では全ロードで十分高速
- **embedding: Ollama → fastembed (BAAI/bge-small-en-v1.5, dim=384)**。onnxruntime==1.17.3 + numpy<2.0 ピンで Intel Mac 動作確認済。Ollama デーモン不要になった
- **bge-small の query prefix (`query_embed`) は本ワークロードでは効果なし**。passage/query 非対称は recall に寄与せず、通常 `embed` で十分
- **security.py を hound から移植**: `validate_server_host` で 127.0.0.1 以外は起動拒否 (exit 1、`CODE_RAG_ALLOW_UNSAFE_BIND=1` でのみ通過)。data/logs/db は 0700/0600。bearer token は不採用 (SSH トンネル前提、CONNECT.md 参照)
- **watcher.py も SQLite 化**: content_hash 差分 upsert + repos.chunk_count 同期更新。実地確認: ファイル変更 +2 -1 → 復元 +1 -2 で 582 chunks に一致

## Phase 4 で踏んだ罠 (再発防止メモ)

- **BM25 同点スコアで候補全落ちする潜在バグ**: `sorted(zip(scores, rows), reverse=True)` はスコア同点時にタプル第 2 要素の dict 比較へ落ちて TypeError → `hybrid_search` 内の `except Exception: pass` が握りつぶし、BM25 候補がゼロのまま検索が「成功」していた。`key=lambda t: t[0]` で修正 (MRR 0.80→0.83)。**広い try/except は品質バグを隠す** — eval の数値変化だけが発見経路だった
- **flashrank は docstring に query 語を含むチャンクを過大評価**: q03 (`generate OpenAPI schema operation paths`) で routing.py の HTTP メソッド系チャンク 11 個 (docstring に同語句) に 0.998 を付け、本命 `get_openapi` (vector 1 位 + BM25 1 位) を top-5 圏外へ降格。対策 = **consensus guard**: RRF 1 位 (vector+BM25 合意) を最終 top-k に保証挿入。これで 0.90 → 0.95
- **eval/run.py の label は位置引数**: `--label foo` と書くとラベルが `--label` になる。`uv run python eval/run.py <label>`
- **launchd 旧サービスは `*:8765` (0.0.0.0) で LAN 公開されていた**: 新コード切替 (unload → load) で 127.0.0.1 bind に是正。`lsof -nP -iTCP:8765 -sTCP:LISTEN` で要確認
- **旧 data/logs の 0644 ログ**: `runtime_permission_warnings` が検出 → 一括 chmod 600 済。旧 `lance.db*` は残置 (ロールバック用)

## Phase 5 確定事項 (Search Debug + FTS5 移行、2026-06-12)

- **search_code_debug ツール追加**: search_code と同一ランキングのまま、段階別スコア内訳 (vector / bm25 / rrf / rerank / diversity_dropped / consensus_guard / final) を返す。`hybrid_search(debug_info=dict)` で収集、返り値・既存ツールは不変。q03 の「flashrank が docstring チャンクを過大評価 → consensus guard が RRF 1 位を救済」がそのまま可視化できることを確認済
- **BM25 を rank_bm25 (オンメモリ) → SQLite FTS5 に移行**: `chunks_fts` 仮想テーブル (chunk_id/repo UNINDEXED + code、unicode61)。起動時の全 chunk RAM ロード廃止、インデックスは DB 内永続。`bm25()` は smaller-is-better なので符号反転して RRF へ。indexer/watcher が chunks と同期 (delete→insert)、`get_db` が旧 DB を自動バックフィル (fts 0 件 && chunks あり)
- **eval: recall@5 0.95 維持 (miss は q12 のみ)、MRR 0.83、avg lat 2460ms** (`eval/results/fts5_migration_20260612T143612Z.*`)。rank-bm25 依存は除去済
- **FTS5 のトークン分割は rank_bm25 と異なる**: unicode61 は `get_openapi(self,` を get / openapi / self に分割 (旧実装は空白 split で 1 トークン)。クエリ側は preprocess_query のトークンを各々 `"..."` 引用句で OR 結合 — 引用句内はフレーズ扱い (隣接一致) なので `"get_openapi"` は実際の出現にマッチする。同点 recall を eval で確認済
- **invalidate_bm25_cache は名前維持で実態は vector cache 破棄のみ** (BM25 は DB 内に常駐するため無効化不要)。watcher/server の呼出し箇所は無修正

## Phase 6 確定事項 (コードグラフ探索 = specs/002、2026-06-13)

- **edges テーブル新設** (graph.py が抽出、indexer/watcher が同期): fastapi リポで defines 390 / imports 418 / references 2260 (計 3068 edges、582 chunks に対し約 5 倍)。SQLite で余裕、BFS depth 2 も体感即時
- **find_references がグラフ照会に昇格** (Phase 1 からの宿題解消)。definition/reference 種別付き、グラフミス時は grep フォールバック + `approximate: true`。observe ログに fallback_reason (graph_miss / graph_error) を記録
- **related_code 新設**: 双方向 BFS (imports/imported_by, defines/defined_in, references/referenced_by)、depth 1-3 / 上限 50 丸め。**ホップ内は imports → defines → references の構造優先順** — routing.py のような defines 50+ のファイルで打ち切り枠が defines に食い潰されるのを防ぐ
- **references 抽出は call + クラス継承のみ** (裸の identifier は拾わない)。全 identifier だと edge が爆発し S/N が下がる (research.md R4)
- **eval: recall@5 0.95 / MRR 0.83 / avg 2462ms — 完全不変** (`eval/results/code_graph_search_20260612T151209Z.*`)。retrieval.py 非接触の理論どおり
- **watcher 実地確認**: import 追記 → 約 10 秒で edge 反映、復元 → edge 消滅、582 chunks 不変

## Phase 6 で踏んだ罠・レビュー知見 (再発防止メモ)

- **テストの日付境界フレーク**: test_observe_metrics が `date.today()` (ローカル JST)、observe.py は UTC 日付でログファイル名生成 → JST 0 時〜9 時に全滅する潜在バグが日付跨ぎで顕在化。**テストの日付はプロダクションコードと同じタイムゾーン (UTC) で生成する**
- **相互 import ノードは BFS で片方向のみ出力** (visited 管理の仕様)。routing.py ⇄ utils.py のようなペアでは imported_by が出ず imports 側だけ見える — バグではない
- **セキュリティ監査の採用修正**: ①`_resolve_module` にリポ封じ込め (深い相対 import `from ....x` がリポ外を stat して edges に記録する穴) ②related_code の target 判定強化 — `/` や先頭 `.` を含む文字列はパス扱い固定 (検証失敗 → path_outside_repos、シンボルへのフォールスルー禁止)、シンボルは `[A-Za-z_][\w.]*` 検証 (invalid_target) ③watcher にリポ内パスガード ④相対 import レベルのルート止め ⑤watcher の chunks+edges を同一コミット境界に (FR-006)
- **レビュー見送り**: admin_reindex の認証 (001 で bearer 不採用決定済、loopback + SSH トンネル前提)、BFS frontier の kind 混在 (ファイルパスとシンボル名は `/` の有無で衝突不能)、_walk_tree の再帰深度 (fastapi 全ファイルでパース成功実証済)

## Phase 7 確定事項 (マルチ repo リモート RAG = specs/003、2026-06-20)

- **マルチ repo 化**: `config.yaml` の `repos[]` に複数 repo 登録。全 MCP ツールに `repo` 引数追加 (必須)。接続レベルの repo 宣言: SSE = `X-Repo` HTTP ヘッダ、stdio = `CODE_RAG_REPO` env。暗黙 default 廃止 → 未指定は `repo_not_specified` エラー
- **評価台分離**: fastapi コーパスを通常運用 `repos[]` から除外し `eval/config.eval.yaml` に分離。`eval/run.py` は DB 索引 (repo "default") を直接計測。引数なし `uv run python eval/run.py` は従来通り fastapi を計測する不変条件を維持
- **差分インデックス**: `indexer.py` が `content_hash` で未変更 chunk の再 embed をスキップ。削除ファイルの chunk/edges を除去 (FR-006/SC-004)
- **リモート取り込み**: `scripts/sync_repo.sh <repo> <src>` で rsync 差分転送 → `data/snapshots/<repo>/` (0700) → 差分索引。`locking.py` (fcntl.flock) で repo 単位の排他ロック → 同時同期を直列化
- **検索契機の自動同期 (任意)**: `hooks/client/pre-search-sync.sh` (PreToolUse フック) で検索直前に同期。サーバ側機構 (差分 index・削除反映・直列化) は pytest で自動被覆済
- **eval: recall@5 0.95 維持 (miss は q12 のみ)、MRR 0.83、avg lat 2460ms** (`eval/results/spec003_postmigration_20260620T002442Z.*`)。ランキング非介入の確認
- **セキュリティレビュー**: 脆弱性発見なし。server.py の `_resolve_repo` は `re.fullmatch(r'[A-Za-z0-9_-]+', repo)` で入力検証済

## Phase 7 で踏んだ罠・知見 (再発防止メモ)

- **fastmcp SSE で `?repo=` URL クエリパラメータは読めない**: T006a PoC で確定。fastmcp がクエリパラメータを透過しないため、接続別 repo 宣言は `X-Repo` HTTP ヘッダに決定 (`.mcp.json` の `headers` で宣言)
- **watcher が `data/snapshots/<repo>/` 未作成で `skip missing path` を繰り返す**: 異常ではなく正常動作 (初回 sync_repo.sh 実行前)。ログレベルは info 相当で設計意図通り
- **post-commit hook の `CODE_RAG_REPO:-default` が暗黙 default 廃止後に 404 する**: specs/003 のスコープ外 (C5) だが要注意。対象 repo を `CODE_RAG_REPO` env で明示する必要あり

