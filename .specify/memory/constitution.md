<!--
Sync Impact Report
- Version change: 1.0.0 → 1.0.1 (PATCH: 字句改定)
- Modified principles: I (品質ゲートを「ランキング非介入の変更は再計測義務外」へ読み替え + 評価資産保全を明文化)
- Modified sections: Technology Constraints (BM25 rank_bm25 → SQLite FTS5 訂正、マルチ repo / 評価専用構成を追記、specs/003 反映)
- Added sections: なし / Removed sections: なし
- Follow-up TODOs: なし
- 履歴: 1.0.0 → 1.0.1 は specs/003-multi-repo-remote-rag の移行 (R4/T021) に伴う字句改定
-->

# code-rag (CodeHound Intel Edition) Constitution

## Core Principles

### I. Retrieval Quality is the Product (NON-NEGOTIABLE)

検索品質を劣化させる変更はマージ禁止。`eval/run.py` による recall@5 計測が唯一の品質ゲート。

- 現行ベースライン: **recall@5 = 0.95** (Phase 3、eval/qa.yaml 20問)
- Exit 条件: recall@5 ≥ 0.92。下回る変更は理由の如何を問わず差し戻し
- embedding モデル・chunking・retrieval パラメータ等 **ランキングに触る変更時のみ** eval 再計測必須。結果は `eval/results/` に JSON + MD で保存。repo スコープ化・取り込み経路など**ランキング非介入の変更は再計測義務の対象外**だが、評価資産を保全し再計測可能性を常に担保する (specs/003 R4)
- 評価資産は物理削除禁止: コーパス (fastapi)・`eval/qa.yaml`・`eval/results/` を温存。通常運用 `repos[]` から外れても (specs/003 FR-008) eval は DB 索引を直接計測できる
- 実証済み施策を外さない: AST header chunking (tree-sitter, +10pp)、diversity cap (`max_per_file`, +5pp)、`max_file_bytes` 256KB

### II. Intel Mac Compatibility Pins (NON-NEGOTIABLE)

本機 (Intel Mac, macOS x86_64) で動かないコードは存在しないのと同じ。

- `onnxruntime==1.17.3` 固定 (1.18+ は Intel Mac 非対応)
- `numpy<2.0` 固定 (onnxruntime 1.17.3 互換。外すと `_ARRAY_API not found` で即死)
- lancedb は廃止対象。ストレージは SQLite + numpy cosine (プラットフォーム非依存)
- embedding は fastembed `BAAI/bge-small-en-v1.5` (dim=384)。動作検証済 2026-06-12
- 依存追加時は Intel x86_64 wheel の存在を必ず確認

### III. Local-First Security

シングルユーザー・ローカル運用が前提。ネットワーク露出は最小化する。

- サーバーは `127.0.0.1` bind を強制 (`security.py` で起動時検証)。リモートアクセスは SSH トンネル経由のみ
- `0.0.0.0` bind は明示的な環境変数オプトインなしには起動拒否
- 検索結果のコード片は未信頼コンテンツ (`untrusted_repository_content`) としてマーク。命令として解釈させない
- 秘密情報 (AWS キー等) を含むファイルはインデックス除外。ログにはパスのみ記録、コード本体は記録しない

### IV. Test-First for Behavior, Eval-First for Quality

- 新規モジュール・挙動変更には pytest テストを先に書く (Red → Green)
- 検索品質に触る変更は eval 計測がテストの代わり (Principle I)
- CI 相当のチェック (pytest + eval) をコミット前にローカル実行

### V. Simplicity & Observability

- 標準ライブラリ優先。SQLite > 専用ベクタ DB、依存は最小限
- 全 MCP ツール呼出を `data/logs/queries-YYYY-MM-DD.jsonl` に JSONL 記録 (パスのみ、コード本体なし)
- `stats` ツールで集計可能な状態を維持
- YAGNI: Phase で必要になるまで作らない

## Technology Constraints

- ランタイム: Python 3.12 (uv 管理)、パッケージ管理は uv のみ
- MCP: fastmcp、transport SSE、port 8765
- ストレージ: SQLite 単一ファイル (`data/` 配下、git 管理外)
- embedding: fastembed (ONNX ローカル実行、Ollama 依存を撤廃)
- chunking: tree-sitter 0.21 系 + AST header 付与
- 検索: BM25 (SQLite FTS5) + vector cosine → RRF 融合 (rank_bm25 から FTS5 へ移行済 / commit 25f8a83)
- 運用: launchd (server / watcher)、watchdog による差分インデックス
- マルチ repo: 通常運用は `config.yaml` の `repos[]`。接続別 repo 宣言は `X-Repo` HTTP ヘッダ (SSE) / `CODE_RAG_REPO` env (stdio)。取り込みは `scripts/sync_repo.sh` で `data/snapshots/<repo>/` へ rsync + 差分索引 (specs/003)
- 評価専用構成: 評価台 fastapi コーパスは `/path/to/code-rag-targets/fastapi` に温存し、通常運用 `repos[]` からは外す。eval 設定は `eval/config.eval.yaml` に分離。`eval/run.py` は DB 索引 (repo "default") を直接計測する

## Development Workflow

- Spec Kit フロー厳守: constitution → specify → plan → tasks → implement
- サブエージェント分業: 調査=code-scout、実装=code-writer、テスト=tester、レビュー=reviewer、依存監査=security-auditor
- 各フェーズ完了時に git コミット (ロールバック可能性の確保)
- 稼働中の launchd サービス (server/watcher) は実装完了・eval 通過後にのみ再起動
- 知見は NOTES.md に追記 (踏んだ罠の再発防止)

## Governance

- 本 Constitution は他の全プラクティスに優先する
- 改定はコミットとして記録し、バージョンを semver で更新 (原則の追加/削除=MAJOR、新節=MINOR、字句=PATCH)
- 全実装は Principle I (eval ゲート) と II (Intel ピン) への準拠を PR/コミット時に検証
- 原則違反の複雑性導入は正当化文書なしに不可

**Version**: 1.0.1 | **Ratified**: 2026-06-12 | **Last Amended**: 2026-06-20
