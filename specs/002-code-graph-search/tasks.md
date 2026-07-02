# Tasks: コードグラフ探索 (Code Graph Search)

**Input**: Design documents from `/specs/002-code-graph-search/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/graph-tools.md, quickstart.md

**Tests**: Constitution IV (Test-First) により、各ストーリーでテストを実装より先に書く (Red → Green)。検索品質は eval ゲート (T018) が担保 — 本機能はスコア非介入のため不変確認。

**Organization**: ユーザーストーリー単位。US1 (find_references 昇格) が MVP。

## Phase 1: Setup

- [x] T001 indexer.py の DDL に edges テーブル + インデックス 3 本を追加 (data-model.md の DDL が正: repo/src_kind/src/dst_kind/dst/edge_type/path/lineno、idx_edges_repo_src / idx_edges_repo_dst / idx_edges_repo_path)。`CREATE TABLE IF NOT EXISTS` なので旧 DB はそのまま動く (FR-009 の前提) — indexer.py

## Phase 2: Foundational (全ストーリーの前提: 抽出エンジン)

- [x] T002 tests/test_graph.py を新規作成 (tester agent、red): (a) Python ソースから imports edge 抽出 — `import a.b` / `from a.b import c` / 相対 import がリポ内ファイルに解決される、リポ外は dst_kind=external、(b) defines edge — 関数/クラス定義が symbol として抽出される、(c) references edge — call と継承のみ。**コメント・文字列リテラル内の同名語が references に含まれない** (SC-002 の中核)、(d) 非対応拡張子で空リスト (クラッシュしない) — tests/test_graph.py
- [x] T003 graph.py を新規作成 (code-writer agent): `extract_file_edges(path, repo_name, repo_root) -> list[tuple]` — tree-sitter (`tree_sitter_languages.get_parser`) で Python の import 文 (`import_statement` / `import_from_statement`) をモジュール解決 (research.md R2: `a/b.py` → `a/b/__init__.py` の順、相対 import はファイルディレクトリ起点)、defines は chunking._extract_symbols 再利用、references は `call` ノードの関数名 + クラス継承の親名のみ (R4)。T002 green — graph.py
- [x] T004 indexer.index_repo に edges 全量再構築を組込み: チャンク収集ループと同じファイル列で `DELETE FROM edges WHERE repo=?` → extract_file_edges を executemany INSERT (FR-007)。tests/test_indexer_sqlite.py に edges 行数 > 0 と再インデックスで重複しないテストを追記 — indexer.py, tests/test_indexer_sqlite.py
- [x] T005 フル reindex 実行: `uv run python indexer.py --repo default` → 582 chunks 維持 + edges の imports/defines/references 各 > 0 を確認 (quickstart §2) — data/code_rag.db (生成物)

**Checkpoint**: 抽出エンジン稼働。`uv run pytest tests/test_graph.py` green

## Phase 3: User Story 1 - シンボル参照の正確な追跡 (P1) 🎯 MVP

**Goal**: find_references をグラフ照会に昇格、グラフミス時は grep フォールバック + approximate 明示

**Independent Test**: quickstart §3 — `get_openapi` で definition/reference 種別付き・approximate=False、未知シンボルで approximate=True

- [x] T006 [US1] tests/test_graph.py に照会テストを追加 (tester agent、red): `find_refs` が defines→definition / references→reference を返す、同名多重定義で全候補、未知シンボルで空 (フォールバック判定は server 側) — tests/test_graph.py
- [x] T007 [US1] graph.py に `find_refs(conn, repo, symbol, limit=50) -> list[dict]` を実装: data-model.md の照会パターン (defines / references を UNION、definition 先頭・lineno 昇順、limit は definition 優先)。T006 green — graph.py
- [x] T008 [US1] server.py の find_references を書換え: graph.find_refs 照会 → 1 件以上なら contracts/graph-tools.md の形式 (path/lineno/kind/approximate=False/context_trust)、0 件なら既存 grep_code フォールバックに kind="reference"/approximate=True を付与。log_call に fallback フラグ (FR-002, FR-010) — server.py
- [x] T009 [US1] quickstart §3 実施: get_openapi で両種別 + approximate=False、コメント内のみの語で構造結果に混入なし (SC-002/SC-003 サンプル確認)、未知シンボルでフォールバック — 検証のみ

**Checkpoint**: MVP。git コミット

## Phase 4: User Story 2 - 検索ヒットの近傍展開 related_code (P2)

**Goal**: シンボル/ファイル起点の深さ制限付き BFS 展開

**Independent Test**: quickstart §4 — routing.py 起点 depth=1 で imports/imported_by/defines が返る、depth=100 が 3 に丸まる

- [x] T010 [US2] tests/test_graph.py に BFS テストを追加 (tester agent、red): 深さ 1/2 の展開結果、循環 import で無限ループしない、max_results 打ち切りで truncated=True、external ノードから展開しない、未知 target で error="target_not_found" — tests/test_graph.py
- [x] T011 [US2] graph.py に `bfs_expand(conn, repo, target, depth, max_results) -> dict` を実装: target 解決 (file → edges の src/path 照合、symbol → defines の dst 照合)、1 ホップずつ順方向 (`src IN`) + 逆方向 (`dst IN`)、方向別 edge_type 命名 (imports/imported_by 等、contracts 準拠)、訪問済み set、depth 1..3 / max_results 1..50 丸め。T010 green — graph.py
- [x] T012 [US2] server.py に related_code ツールを追加: contracts/graph-tools.md の署名・返却形 (target/depth/related/truncated/error/context_trust)。ファイルパス target は `_validate_path` 検証 (リポ外 → error="path_outside_repos")、log_call 記録 (FR-003, FR-004, FR-008, FR-010) — server.py
- [x] T013 [US2] quickstart §4 実施: routing.py 起点 depth=1、depth=100 丸め、存在しない target — 検証のみ

**Checkpoint**: git コミット

## Phase 5: User Story 3 - グラフの自動鮮度維持 (P3)

**Goal**: watcher の差分インデックスで edges も同期

**Independent Test**: quickstart §6 — import 追加 → edge 出現、ファイル削除 → edge 消滅

- [x] T014 [US3] tests/test_graph.py に watcher 同期テストを追加 (tester agent、red): RepoWatcher._apply を直接呼び、ファイル変更で当該 path の edges が引き直される、削除で消える (tmp_path のミニリポ + 実 DB 接続、test_indexer_sqlite.py の fixture 流用) — tests/test_graph.py
- [x] T015 [US3] watcher.py の _apply / _delete_path に edges 同期を追加: 変更ファイルは `DELETE FROM edges WHERE repo=? AND path=?` → extract_file_edges 再 INSERT、削除ファイルは DELETE のみ。chunks 更新と同じ _apply_lock / commit 境界 (FR-006)。T014 green — watcher.py
- [x] T016 [US3] watcher 同期の実地確認は T019 (サービス再起動後) に統合 — 検証のみ

**Checkpoint**: 全 FR 実装完了

## Phase 6: Polish & Cross-Cutting

- [x] T017 `uv run pytest -q` 全 green (54 + 新規) — tests/
- [x] T018 eval ゲート: `uv run python eval/run.py code_graph_search` で recall@5 ≥ 0.92 (期待 0.95 / miss=q12 のみ、retrieval.py 非接触の不変確認)。結果を eval/results/ にコミット対象として保存 (SC-001) — eval/results/
- [x] T019 launchd 両サービス再起動 (eval 通過後のみ)、127.0.0.1 bind 確認、稼働 watcher で quickstart §6 実地確認 (import 追加 → edge 反映 → 復元)、reviewer + security-auditor 監査 — ~/Library/LaunchAgents/
- [x] T020 NOTES.md に Phase 6 確定事項 (edges 規模・抽出知見・踏んだ罠) 追記、README.md の MCP ツール一覧に find_references 強化 / related_code / search_code_debug を反映、git コミット — NOTES.md, README.md

## Dependencies & Execution Order

- Phase 1 (T001) → Phase 2 (T002→T003→T004→T005) → US1 → US2 → US3 → Polish
- US1 (T006-T009) と US2 (T010-T013) は graph.py の関数が独立のため、T007/T011 完了後は並行可。ただし server.py 編集 (T008/T012) は直列
- US3 (T014-T015) は T003 完了後ならいつでも可 (watcher は server 非依存)
- [P] なし — 小規模 1 ファイル中心の直列フローのため、並行は subagent 単位 (tester が次ストーリーのテストを先行作成可) で行う

## Implementation Strategy

MVP = Phase 1-3 (US1)。find_references の昇格だけで Phase 1 からの宿題が解消し価値が立つ。以降 US2 (related_code) → US3 (watcher) を積み、フェーズ毎にコミット。サブエージェント分担: テスト=tester、graph.py 実装=code-writer、server/indexer/watcher 統合と検証=オーケストレーター直接 (Bash 必要)、最終レビュー=reviewer + security-auditor。eval とサービス再起動はオーケストレーターのみ。
