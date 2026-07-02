---
description: "Task list for 複数 dev コンテナ共有・汎用マルチ repo RAG サーバ化"
---

# Tasks: 複数 dev コンテナ共有・汎用マルチ repo RAG サーバ化

**Input**: Design documents from `/specs/003-multi-repo-remote-rag/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (mcp-tools.md, sync-cli.md), quickstart.md

**Tests**: 含む。plan.md が `tests/test_repo_resolution.py` / `test_sync_serialize.py` / `test_hygiene_multi.py` を明示。Constitution IV (Test-First) に従い、repo 解決・直列化・除外は pytest 先行。

**Organization**: タスクは user story 単位。各ストーリーは独立実装・独立テスト可能。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並行可 (別ファイル・依存なし)
- **[Story]**: US1=repo スコープ / US2=リモート取り込み / US3=自動鮮度 / US4=評価台移行
- 既存フラット構成 (ルート直下 Python モジュール群)。`src/` 階層化なし (plan Structure Decision)

## Path Conventions

- ソース: ルート直下 (`server.py`, `indexer.py`, `hygiene.py`, `config.yaml`)
- 新規: `scripts/sync_repo.sh`, `hooks/` 配下手順, `tests/` 配下 3 ファイル
- テスト: `tests/` (ルート直下)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: マルチ repo の土台。スキーマ変更は不要 (data-model.md: 既存 `repo` 列で済)。

- [X] T001 サーバ側保管ディレクトリ `data/snapshots/` を新設し `.gitignore` に追加 (取り込みスナップショット置き場、git 管理外)。**`0700` 権限で作成 (所有者限定、平文複製の閲覧限定 / FR-014・Principle III)**
- [X] T002 [P] `config.yaml` を multi-repo 構造へ拡張: `default`/fastapi を `repos[]` から外し評価専用へ降格、`market_brief` (path: `data/snapshots/market_brief/`, languages は実装時 Market_Brief 構成を確認して設定) を追加。通常運用 repo の `hygiene.extra_exclude_dirs` は空 (一般用途)。評価用 config (`eval/config.eval.yaml`) を分離し fastapi コーパス参照 + fastapi 固有 exclude (`docs_src`/`docs`/`scripts`) と `max_file_bytes: 262144` を**温存** (chunk 数不変、eval 比較を壊さない / Constitution I)
- [X] T003 [P] `tests/conftest.py` を新設 (既存 tests/ に conftest 無し): 一時 DB + 2 repo フィクスチャを追加。既存 5 テスト (test_graph 等) を壊さない

**Checkpoint**: config が N repo 前提・評価台分離済。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 全 user story が依存する repo 解決基盤。**完了まで US 着手不可。**

**⚠️ CRITICAL**: 解決順ロジックが無いと US1〜US3 すべて成立しない。**T006a (PoC) と T007a (差分) は他タスクのブロッカー。先行必須。**

> **番号順 ≠ 実行順**: ブロッカーを先頭に置くため ID は `T006a → T004 → T005 → T006 → T007 → T007a` と非連続。実行順は本フェーズ内記載と末尾「Within Each User Story」に従う (ID の昇順ではない)。

- [X] T006a 🔴 **[ブロッカー/PoC] — 完了 (2026-06-20)**: fastmcp 3.2.4 で実 E2E PoC。**結論: `?repo=` URL クエリは不可** (ツール呼出は別 POST /messages で来て query が消える)。**`X-Repo` HTTP ヘッダは可** (`get_http_headers().get("x-repo")` で接続別に読める)。→ **ヘッダ方式 (`X-Repo`) を採択**、`?repo=` 不採用、B1/B2 縮退も不要。stdio 時のみ `CODE_RAG_REPO` env。T004/T006 はこの結論に従う (research R1 / [[fastmcp-sse-repo-declaration]])
- [X] T004 `server.py` に repo 解決ヘルパ `_resolve_repo(arg_repo)` を実装: 解決順「明示引数 > 接続宣言 > エラー」。接続宣言の取得元は T006a 確定経路 (SSE=`X-Repo` ヘッダ、stdio=`CODE_RAG_REPO` env)。空文字・空白のみは未宣言扱い (data-model / FR-002・FR-003)
- [X] T005 `server.py` に共通エラー応答ヘルパを実装: `repo_not_specified` / `repo_not_found` を `available_repos` 付きで返す (contracts/mcp-tools.md / FR-004)。`_resolve_repo_path` (server.py:34) の `"default"` 既定を除去
- [X] T006 T006a 確定経路の実装: 接続宣言 (SSE=`X-Repo` ヘッダ via `get_http_headers()` / stdio=`CODE_RAG_REPO` env) を読む。読めない場合はエラー (暗黙 default 禁止)
- [X] T007 repo 単位ロック機構を実装 (ファイルロック `data/locks/<repo>.lock` or サーバ内ロック)。同一 repo の同期/インデックス直列化の共通基盤 (FR-012)
- [X] T007a 🔴 **[ブロッカー]** `indexer.py` `index_repo` に真の差分モードを実装 (C7)。**現状フル `--repo` は L266-268 で全 chunk 削除 → L337 全ファイル再 embed で FR-006/SC-004 違反**。設計: フル走査で削除反映を維持しつつ、各 chunk の `content_hash` が DB 既存と一致なら embed をスキップ (未変更ファイルは再計算しない)、現存しないファイルの chunk/edges は削除。`repo=="all"` 等の既存分岐に副作用が無いか確認 (FR-006・SC-004)

**Checkpoint**: repo 解決経路確定 (T006a) + 差分インデックス (T007a) + エラー応答 + ロックが揃い、各ストーリー着手可。

---

## Phase 3: User Story 1 - 複数コンテナが自分の repo だけを検索 (Priority: P1) 🎯 MVP

**Goal**: 接続宣言した repo に検索をスコープし、未指定は暗黙 default せずエラーで指定を促す。

**Independent Test**: 2 repo 登録 → 片方宣言クライアントの検索 → その repo のヒットのみ。宣言も明示も無い → `repo_not_specified` + available_repos。

### Tests for User Story 1 ⚠️

> 先に書き、FAIL を確認してから実装

- [X] T008 [P] [US1] `tests/test_repo_resolution.py`: 解決順 (明示>宣言>エラー)、存在しない repo、空文字/空白の各分岐をテスト (Acceptance 1-4 / SC-001・SC-002)

### Implementation for User Story 1

- [X] T009 [US1] `server.py` `search_code` / `search_code_debug` の `repo` 既定を `"default"` → `None` へ変更し、`_resolve_repo` + エラー応答を適用 (ランキング・返却フォーマット不変 / FR-011)
- [X] T010 [US1] `server.py` の残り repo スコープ系ツール **`grep_code` / `find_references` / `related_code` / `get_file_range` (server.py:235) / `list_symbols` (server.py:408)** の `repo` 既定を `None` へ変更し共通解決順を適用、既存 `repo_not_found` を共通フォーマットへ統一 (C2/C3: list_symbols 欠落・get_file_range 不一致を修正)
- [X] T010a [US1] `retrieval.py` `search` (retrieval.py:373) の `repo="default"` 既定を除去 (plan:68)。server が必ず解決済 repo を渡す方針なので既定を `None` 化 (or 引数必須化)。`repo=="all"` 分岐 (retrieval.py:226) が list_repos スコープ漏れに繋がらないか確認 (C4)
- [X] T011 [US1] `server.py` に新規 MCP ツール `list_repos` を追加: 通常運用 repo の name/languages/chunks/last_indexed を返す。評価台 (fastapi) は含めない。既存 `_log`/observe パターンで呼出を `data/logs/queries-*.jsonl` に記録 (contracts/mcp-tools.md / SC-006 / Constitution V 全 MCP ツール呼出記録)

**Checkpoint**: US1 単独で動作・テスト可能 (MVP)。

---

## Phase 4: User Story 2 - リモート作業機のコードを取り込んで検索 (Priority: P1)

**Goal**: 作業機 → サーバへ rsync 差分転送し、差分インデックスで repo を検索可能化。

**Independent Test**: `sync_repo.sh market_brief <src>` → `list_repos` に出現・検索ヒット。変更ゼロ再実行で重い再計算なし。削除ファイルのチャンク+edges が消える。

### Tests for User Story 2 ⚠️

- [X] T012 [P] [US2] `tests/test_sync_serialize.py`: 同一 repo へ同期/インデックス二重起動 → ロックで直列化・索引非破損 (FR-012 / quickstart 直列化確認)
- [X] T013 [P] [US2] `tests/test_hygiene_multi.py`: `node_modules`/巨大ファイル/秘密情報/`.gitignore` 対象が全 repo でスナップショット・索引に入らない (FR-009)

### Implementation for User Story 2

- [X] T014 [US2] `scripts/sync_repo.sh <repo_name> <src_path>` を新規作成: rsync over SSH トンネル (127.0.0.1 ローカルフォワード)、`--delete`、**パス/パターン除外のみ** (`.git`/`node_modules`/生成物/`.gitignore`/`.env` 等。内容ベース秘密検知は hygiene 段の責務 — rsync は内容走査しない / FR-009)、`--max-size` は hygiene `max_file_bytes` (既定 262144) と揃える、**保管先 `data/snapshots/<repo_name>/` を `0700` 権限で作成・維持 (FR-014)**、終了コード 0=成功(変更ゼロ含む)/非0=転送失敗 (contracts/sync-cli.md / FR-005・FR-013・FR-009・FR-014)
- [X] T015 [US2] `sync_repo.sh` 末尾でサーバ側インデックス `indexer.py --repo <repo_name>` をトリガし、T007 のロックで直列化。T007a の差分モードにより未変更ファイルは再 embed されない。変更ゼロ時は重い再計算なしで即座に返る (FR-006・FR-007・SC-004)
- [X] T016 [US2] `hygiene.py` の除外ルール (大量生成物/巨大ファイル/**秘密情報の内容ベース検知**/`.gitignore`) が全 repo に一様適用されることを確認・必要なら修正。**秘密検知は索引段 (hygiene) の責務 (rsync 段はパターンのみ)。****通常運用 repo のみ** `extra_exclude_dirs` を空 (一般化)。fastapi 固有 exclude は T002 の評価用 config 側で温存し、評価台 chunk 数を変えない (FR-009 / Constitution I)
- [X] T017 [US2] `tests/test_indexer_delete.py` (新規): rsync `--delete` 相当でファイル削除後、T007a 差分インデックスで該当 chunk + chunks_fts + edges が消えることを **pytest 検証** (確認止まりにしない / Acceptance US2-3)

**Checkpoint**: US1+US2 で「手動同期 + 検索」が end-to-end 動作。

---

## Phase 5: User Story 3 - 検索時に最新コードが自動反映 (Priority: P2)

**Goal**: 作業機側 pre-tool-use フック、またはファイル監視常駐が同期を起動し、最新コードを反映。変更ゼロなら待ち時間ほぼゼロ。

**Independent Test**: 作業機で編集 → 直後検索 → 反映。編集せず連続検索 → 2 回目以降即座に返る。

> **SC-005 テスト被覆 (Constitution IV 整合)**: 鮮度の**サーバ側機構** (rsync 差分 → `index_repo` 差分 → 検索反映) は自動テスト済 — T012 (直列化)・T015/T007a (差分 index)・T017 (削除反映) がカバー。**クライアント側 pre-tool フック自体は手順書 + サンプル (T018/T019) で pytest 不能** (作業機 Claude Code 上で動くため)。フック経路の end-to-end は T024 で手動検証。この役割分担を明記し「自動テストなし」を意図的な範囲として残す。

### Implementation for User Story 3

- [X] T018 [US3] `hooks/client/` (新規。既存 `hooks/post-commit` は git hook なので役割分離 / C5) にクライアント側 pre-tool-use フックのサンプルを追加 (検索ツール実行前に `sync_repo.sh <repo> <src>` を同期実行 → 完了待ち → 検索)。サーバはフック非依存。**これは FR-007a (検索契機の自動同期 = SHOULD) の実現。フック無し時は FR-007 の手動同期でフォールバック** (contracts/sync-cli.md / FR-007a)
- [X] T019 [US3] `hooks/client/README.md` に作業機側 Claude Code 設定手順を記述: SSH トンネル確立、接続宣言 (**SSE = `X-Repo` ヘッダ** / T006a 確定経路)、フック登録 (research R5 環境固有値の外出し)
- [X] T026 [US3] 作業機側ファイル監視常駐を実装・採択: `scripts/watch_and_sync.sh` (fswatch 2秒デバウンス → `sync_repo.sh`) + launchd 常駐 plist (SSH トンネル維持 / watcher 維持の2本)。**Dev Container 運用で pre-tool-use フック (T018) がコンテナ→Mac パス到達不能なため、編集契機の常駐監視を FR-007a の実装として採択** (T018/T019 のフック方式はホスト直運用向け代替として温存)。end-to-end 稼働確認済 (2026-07-02)

**Checkpoint**: US1〜US3 で厳密鮮度 (SC-005) 達成。

---

## Phase 6: User Story 4 - 評価専用構成からの脱却 (Priority: P3)

**Goal**: 評価台 (fastapi) を通常運用から外しつつ評価資産を保全し、再計測可能に保つ。

**Independent Test**: `list_repos`/通常検索に fastapi が現れない。評価資産温存で `eval/run.py` 再計測可能。

### Implementation for User Story 4

- [X] T020 [US4] `eval/run.py` を評価用 config (T002 の `eval/config.eval.yaml` or `--repo fastapi` 明示) で fastapi コーパスを対象化するよう調整。評価資産 (`code-rag-targets/fastapi` + `eval/qa.yaml` + `eval/results/`) は物理削除せず温存 (FR-008・SC-006)。**🔴 CLAUDE.md 不変条件保護: 引数なし `uv run python eval/run.py` が従来通り fastapi を計測する (= `eval/config.eval.yaml` を既定で読む、または fastapi を既定 repo にする) ことを明記・検証。さもなくば CLAUDE.md「recall@5 ≥ 0.92 (`uv run python eval/run.py`)」と T023/SC-007 の呼出前提が壊れる**
- [X] T021 [US4] `.specify/memory/constitution.md` の「評価対象リポジトリ」記述を本移行に合わせ改定 (品質ゲート = ランキング変更時のみ再計測へ読み替え / spec Assumptions・research R4)。併せて stale 記述「BM25 (rank_bm25)」を FTS5 へ訂正 (commit 25f8a83 で移行済 / PATCH 改定)

**Checkpoint**: 全 user story が独立動作。

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T022 [P] `CLAUDE.md` / `README` のマルチ repo 運用・取り込み手順を更新 (docs/)
- [X] T023 SC-007 品質非介入の確認: `uv run python eval/run.py` で recall@5 ≥ 0.92 を移行前後で同値確認 (Constitution I)
- [X] T024 quickstart.md の 4 シナリオを手動検証 (repo スコープ / 取り込み / 鮮度 / 評価台移行)。SC-004/SC-005 の「待ち時間無視」は定性のため、変更ゼロ同期の追加待ちの目安閾値 (例: < 数秒) を quickstart に補記して判定基準を明確化 (C8)
- [X] T025 [P] `security.py` の 127.0.0.1 bind 強制・検索結果の未信頼コンテンツ標識・ログはパスのみ、が全変更後も維持されることを確認 (FR-010)。**併せて `data/snapshots/` が `0700` 権限・git 管理外で着地していることを確認 (FR-014)**

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 依存なし。即着手可
- **Foundational (Phase 2)**: Setup 後。**全 US をブロック**。内部順序: **T006a (PoC, 接続経路確定) → T004→T005→T006**、**T007a (差分インデックス) は US2 のブロッカー**。T006a が落ちると US1 全滅 → 最優先
- **User Stories (Phase 3-6)**: Foundational 後。優先度順 P1(US1→US2) → P2(US3) → P3(US4)
- **Polish (Phase 7)**: 全 US 完了後

### User Story Dependencies

- **US1 (P1)**: Foundational のみ依存。独立テスト可
- **US2 (P1)**: Foundational 依存 (特に **T007a 差分インデックス**)。`list_repos` (T011) で出現確認するが、同期/インデックス自体は US1 と独立
- **US3 (P2)**: US2 の `sync_repo.sh` (T014/T015) に依存 (フックがそれを呼ぶ)
- **US4 (P3)**: T002 (config 分離) に依存。他 US と独立

### Within Each User Story

- テスト (T008/T012/T013) を先に書き FAIL 確認 → 実装
- T004→T005→T009/T010 (解決ヘルパ → エラー応答 → ツール適用)
- T014→T015 (rsync → インデックストリガ)

### Parallel Opportunities

- Setup: T002, T003 並行
- US1 テスト T008、US2 テスト T012/T013 は別ファイルで並行可
- Foundational 完了後、US1 と US4 は別開発者で並行可 (US2→US3 は直列寄り)
- Polish: T022, T025 並行

---

## Parallel Example: User Story 2 テスト

```bash
# US2 のテストを同時起動 (別ファイル):
Task: "tests/test_sync_serialize.py の直列化テスト"
Task: "tests/test_hygiene_multi.py の除外テスト"
```

---

## Implementation Strategy

### MVP First (User Story 1 のみ)

1. Phase 1 Setup → 2. Phase 2 Foundational (CRITICAL) → 3. Phase 3 US1
4. **STOP & VALIDATE**: repo スコープ + 未指定エラーを単独検証 (SC-001/SC-002)

### Incremental Delivery

1. Setup + Foundational → 土台
2. US1 → repo スコープ検索 (MVP)
3. US2 → リモート取り込み (手動同期で end-to-end)
4. US3 → 検索起点の自動鮮度
5. US4 → 評価台移行 + 資産保全

各段で前段を壊さず価値追加。フェーズ毎 git コミット (Constitution / 開発フロー)。eval 通過後にのみ launchd サービス再起動。

---

## Notes

- [P] = 別ファイル・依存なし
- スキーマ変更なし (data-model.md)。本機能は「default 固定箇所を接続宣言+解決順へ」+ 取り込み経路 + 同期トリガに集約
- chunking.py 不変 (Constitution: chunk 数変動は eval 比較を壊す)
- 検索ランキング非介入 (FR-011/SC-007)。embedding/retrieval パラメータ不変
- 環境固有値 (Market_Brief パス・言語・Mac A ホスト名) は config.yaml と手順書に外出し、コードは環境非依存
- US3 の自動鮮度は Dev Container 運用のため検索契機フック → 編集契機ファイル監視常駐 (`watch_and_sync.sh` + launchd) へ実装転換 (FR-007a / T026)。SC-005 は達成
