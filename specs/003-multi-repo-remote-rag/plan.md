# Implementation Plan: 複数 dev コンテナ共有・汎用マルチ repo RAG サーバ化

**Branch**: `003-multi-repo-remote-rag` | **Date**: 2026-06-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-multi-repo-remote-rag/spec.md`

## Summary

単一 repo (`default` = fastapi 評価台) 前提だった code-rag を、複数 repo を同居させ接続単位で repo をスコープする汎用マルチ repo RAG サーバへ移行する。検索対象コードはリモート作業機 (Mac B) にあるため、SSH トンネル上の rsync でサーバ側 (Mac A) の保管場所へ差分転送し、既存の差分インデックスへ流す。検索アクセスを契機にクライアント側フックが同期を起動し、最新コードを反映する。検索ランキング (vector + FTS5 BM25 + RRF + rerank + diversity) には一切触れない。

技術的アプローチ: DB schema・config・retrieval は既に `repo` 列でスコープ可能。残る gap は (1) 接続単位の repo 宣言と解決順 (明示 > 宣言 > エラー、暗黙 default 廃止)、(2) リモート取り込み経路 (rsync スクリプト + サーバ側保管ディレクトリ)、(3) 検索起点の自動同期 (クライアントフック)、(4) 評価台の通常運用からの除外と評価資産保全。

**最大リスクと縮退設計 (FR-015)**: (1) の接続宣言は fastmcp SSE で接続別 `?repo=` をツールが読めるかに依存し未確定 (T006a PoC ブロッカー)。第一経路が落ちると US1 の中核「毎回 repo 指定不要」が崩壊するため、**縮退経路を先に確定する**: (B1) repo 毎に分離した SSE エンドポイントを mount し、マウントパスから repo を一意化 (クライアントは自分の repo の URL に接続 → 宣言不要を維持)。(B2) 接続後に明示宣言する MCP ツール (`use_repo`) でサーバ側セッションへ保持。いずれも暗黙 default へ退避せず US1 の価値命題を保つ。T006a は第一経路の可否判定に加え、不可時にどちらの縮退を採るかまで決める。

## Technical Context

**Language/Version**: Python 3.12 (uv 管理、`>=3.12,<3.13`)

**Primary Dependencies**: fastmcp (SSE transport)、fastembed `BAAI/bge-small-en-v1.5` (dim=384)、onnxruntime==1.17.3、numpy<2.0、tree-sitter==0.21.3、flashrank (rerank)、watchdog (差分監視)。**新規依存追加なし** (rsync/ssh は OS 標準)

**Storage**: SQLite 単一ファイル `./data/code_rag.db` (chunks / chunks_fts / repos / edges)。git 管理外。取り込みスナップショットはサーバ側ファイルシステム上のディレクトリ

**Testing**: pytest (`tests/`)、品質ゲートは `eval/run.py` (recall@5 ≥ 0.92)

**Target Platform**: Intel Mac (macOS x86_64)、RAG サーバ専用機 Mac A 常時稼働

**Project Type**: Single project — MCP サーバ (ルート直下のフラット Python モジュール群)

**Performance Goals**: 変更ゼロ時の連続検索で同期由来の追加待ち時間が無視できる (差分検知のみで重い再計算なし)。検索ランキングは前後で不変 (SC-007)

**Constraints**: 127.0.0.1 bind 強制 (リモートは SSH トンネルのみ)、Intel Mac ピン厳守、chunking 挙動不変 (chunk 数変動は eval 比較を壊す)、検索ランキング非介入

**Scale/Scope**: 初期 1 repo (Market_Brief) + 評価台 (除外)、設計は N repo 前提。クライアントは複数 dev コンテナ (Mac B 上、Mac C から Remote-SSH)

## Constitution Check

*GATE: Phase 0 前に通過必須。Phase 1 設計後に再確認。*

- **I. Retrieval Quality (NON-NEGOTIABLE)**: 本機能は repo スコープ + 同期の追加であり検索ランキングに非介入 (FR-011/SC-007)。embedding・chunking・retrieval パラメータ不変。評価資産は物理保全し、ランキングに触る変更時のみ再計測する運用へ読み替え (spec Assumptions 準拠)。→ **PASS** (ランキング不変を SC-007 で検証)
- **II. Intel Mac Pins (NON-NEGOTIABLE)**: 新規 Python 依存ゼロ。rsync/ssh は OS 標準。ピン (`onnxruntime==1.17.3` / `numpy<2.0`) 不変。→ **PASS**
- **III. Local-First Security**: 127.0.0.1 bind 維持 (FR-010)。リモート作業機 → サーバは SSH トンネル経由 (FR-013、既存トンネル方向と同一)。検索結果は未信頼コンテンツ標識維持。除外ルールを全 repo に適用 (FR-009: rsync 段=パターン/サイズ、hygiene 段=内容ベース秘密検知の責務分担)。取り込みスナップショットは平文複製のため `0700` 権限 + git 管理外で保護 (FR-014)。ログはパスのみ。→ **PASS**
- **IV. Test-First / Eval-First**: repo 解決ロジック・除外ルール・同期直列化は pytest 先行。ランキング非介入なので eval は前後同値の確認に使う。→ **PASS**
- **V. Simplicity & Observability**: 既存 SQLite schema を流用 (新テーブル最小)。MCP ツール呼出ログ維持。YAGNI: 初期 1 repo でも N repo 設計だが、追加コードは config + 解決順 + 取り込みスクリプトに限定。→ **PASS**

**Gate 結果**: 違反なし。Complexity Tracking は空。

## Project Structure

### Documentation (this feature)

```text
specs/003-multi-repo-remote-rag/
├── plan.md              # This file
├── spec.md              # 完成済
├── research.md          # Phase 0 output (本コマンドで生成)
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (MCP ツール契約 + 同期 CLI 契約)
├── checklists/
│   └── requirements.md  # 既存
└── tasks.md             # /speckit-tasks で生成 (本コマンドでは未作成)
```

### Source Code (repository root)

```text
code-rag/                       # ルート直下フラット構成 (既存)
├── server.py                   # MCP ツール群。repo 解決順 + 接続宣言 + repos ツールを追加
├── retrieval.py                # repo スコープ済 (変更最小)。"default" 既定の除去
├── indexer.py                  # 差分インデックス済 (index_repo)。取り込み後に呼ぶ
├── chunking.py                 # 不変 (Constitution)
├── hygiene.py                  # 除外ルール。全 repo 適用を確認
├── security.py                 # 127.0.0.1 bind 検証 (不変)
├── watcher.py                  # 既存差分監視
├── config.yaml                 # repos リスト拡張 (Market_Brief 追加、default/fastapi を eval 専用へ降格)
├── scripts/
│   └── sync_repo.sh            # 新規: 作業機 → サーバの rsync (SSH トンネル経由)
├── hooks/                      # クライアント側 pre-tool フック (手順書 + サンプル)
├── eval/                       # 評価資産。通常 repos から外すが保全
└── tests/
    ├── test_repo_resolution.py # 新規: 解決順 (明示>宣言>エラー)
    ├── test_sync_serialize.py  # 新規: 同一 repo 同期の直列化
    └── test_hygiene_multi.py   # 新規: 除外ルール全 repo 適用
```

**Structure Decision**: 既存のフラット構成を維持 (Constitution V「YAGNI / SQLite > 専用 DB」)。`src/` 階層化は行わない — 既存全モジュールがルート直下にあり、移動は eval/launchd パスを壊す。新規追加は `scripts/sync_repo.sh` (取り込み) と `hooks/` 配下の手順、`tests/` の 3 ファイル、`config.yaml` の repos 拡張に限定。

## Complexity Tracking

> Constitution Check に違反なし。記載不要。
