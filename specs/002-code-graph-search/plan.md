# Implementation Plan: コードグラフ探索 (Code Graph Search)

**Branch**: `main` (ブランチ運用なし、ユーザー主導 git) | **Date**: 2026-06-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-code-graph-search/spec.md`

## Summary

インデックス時に tree-sitter でコード関係 (ファイル間 import / シンボル定義 / シンボル参照) を抽出して SQLite `edges` テーブルに永続化し、(1) `find_references` を grep 近似からグラフ照会に昇格 (0 件時は grep フォールバック + `approximate: true`)、(2) 新 MCP ツール `related_code` で深さ制限付き BFS の近傍展開を提供する。`search_code` / `hybrid_search` のランキングには一切手を入れない (edges はスコア非介入)。watcher は path 単位でグラフを差分同期する。

## Technical Context

**Language/Version**: Python 3.12 (uv 管理)

**Primary Dependencies**: 既存のみ — tree-sitter==0.21.3 + tree-sitter-languages (パーサ取得は `tree_sitter_languages.get_parser(lang)`、chunking.py と同方式)。新規依存ゼロ

**Storage**: 既存 `data/code_rag.db` (SQLite, WAL 済) に `edges` テーブルを同居。DDL は `indexer.DDL` に追加

**Testing**: pytest (`tests/test_graph.py` 新規 + watcher 同期テスト)。検索品質は eval ゲート (`uv run python eval/run.py code_graph_search`) — ただし本機能はスコア非介入のため不変確認

**Target Platform**: Intel Mac (macOS x86_64)。onnxruntime==1.17.3 / numpy<2.0 ピンに非接触

**Project Type**: 既存単一プロジェクト (フラット構成) への増築

**Performance Goals**: related_code 深さ 2 / 約 600 chunks 規模で 1 秒以内 (SC-004)。グラフ抽出はインデックス時間を支配しない (embedding が支配項)

**Constraints**: search_code ランキング不変 (recall@5 ≥ 0.92 ゲート維持、SC-001)。MCP 既存ツール署名不変。127.0.0.1 bind / context_trust 規約維持

**Scale/Scope**: 評価対象 fastapi (~582 chunks / ~100 ファイル)。edges は数千〜1 万行想定 (SQLite で余裕)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | 判定 | 根拠 |
|---|---|---|
| I. Retrieval Quality (recall@5 ≥ 0.92) | PASS | edges は検索スコア非介入 (FR-005)。eval `code_graph_search` で不変確認後にのみサービス再起動 |
| II. Intel Pins | PASS | 新規依存ゼロ。tree-sitter 0.21.3 + tree-sitter-languages は導入済・動作中。ONNX/numpy 非接触 |
| III. Local-First Security | PASS | 新ツールも 127.0.0.1 サーバ内。返却に `context_trust` 付与 (FR-008)。パス検証は既存 `_validate_path` 流用 |
| IV. Test-First | PASS | tests/test_graph.py を実装より先に作成 (Red → Green)。watcher 同期もテスト |
| V. Simplicity & Observability | PASS | 標準ライブラリ + 既存依存のみ。新ツール呼出しは observe.log_call で JSONL 記録 (FR-010) |

違反なし → Complexity Tracking 不要。

## Project Structure

### Documentation (this feature)

```text
specs/002-code-graph-search/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── graph-tools.md   # find_references / related_code の返却契約
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
code-rag/                  # 既存フラット構成を維持
├── graph.py               # 新規: 抽出 (extract_file_edges) + 照会 (find_refs / bfs_expand)
├── indexer.py             # 変更: DDL に edges 追加、index_repo で全量再構築
├── watcher.py             # 変更: path 単位の edges 差分同期
├── server.py              # 変更: find_references 昇格、related_code 追加
├── chunking.py            # 無変更 (graph.py が _extract_symbols / _detect_language を import)
├── retrieval.py           # 無変更 (絶対条件)
└── tests/
    ├── test_graph.py      # 新規: 抽出・BFS・循環・同名多重定義・フォールバック
    └── test_indexer_sqlite.py  # 追記: edges 再構築・孤立 edge なし
```

**Structure Decision**: 既存フラット構成に graph.py を 1 ファイル追加。抽出と照会を同居させる (どちらも tree-sitter / SQLite の同一知識を扱う小規模モジュールのため分割しない — Principle V)。

## 設計の要点 (Phase 0/1 成果物の要約)

- **抽出 (graph.py)**: Python 第一級。`import x` / `from x import y` → モジュール名をリポ内ファイルへ解決 (パッケージルート相対、`x/y.py` と `x/y/__init__.py` の両方を試行)。解決不能は `dst_kind=external` で終端記録。defines は `chunking._extract_symbols` 再利用。references は tree-sitter AST の `call` / `identifier` ノード walk — コメント・文字列はノード種別が異なるため自然に除外 (SC-002 の根拠)
- **同名多重定義**: 絞り込まず全候補を返す (spec Assumptions)。references は名前ベース近似
- **find_references**: edges から definition (`edge_type=defines`) + reference (`edge_type=references`) を種別付きで返す。0 件時は既存 grep_code にフォールバックし全結果へ `approximate: true` 付与 (FR-002, FR-009)
- **related_code**: BFS。深さ default 1 / max 3 (丸め)、返却上限 50 件 (FR-004)。訪問済み set で循環対応。起点はシンボル名 or ファイルパス (リポ相対/絶対両対応、`_validate_path` 検証)
- **indexer**: `DELETE FROM edges WHERE repo=?` → ファイル毎に抽出 INSERT (FR-007)。インデックス 2 本: `(repo, src)` / `(repo, dst)`
- **watcher**: 変更/削除ファイルの path で `DELETE FROM edges WHERE repo=? AND path=?` → 再抽出 INSERT。chunks と同一コミット境界 (FR-006)
- **旧 DB マイグレーション**: `get_db` は DDL 追加のみ (CREATE TABLE IF NOT EXISTS)。edges 空でもツールはフォールバックで動作 (FR-009)。完全なグラフは全量 reindex で構築 — quickstart に手順記載

## Complexity Tracking

違反なし — 記載不要。
