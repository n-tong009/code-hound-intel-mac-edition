# Phase 1 Data Model: 複数 dev コンテナ共有・汎用マルチ repo RAG サーバ化

spec の Key Entities を既存 SQLite schema (`indexer.py`) と config 構造へ写像する。**既存 schema は既に `repo` 列でスコープ済**。本機能での追加は最小。

## エンティティ

### repo (リポジトリ)

検索対象の論理単位。複数併存し、全検索・参照系が repo でスコープされる。

- **定義場所**: `config.yaml` の `repos[]` (静的設定) + DB `repos` テーブル (索引メタ、既存)。
- **フィールド**:
  - `name` (TEXT, PK) — repo 名。接続宣言・検索引数のキー。例 `market_brief`
  - `path` (TEXT) — サーバ側のインデックス入力パス。取り込み運用では `data/snapshots/<name>/`
  - `languages` (list) — 対象言語。chunking/hygiene のフィルタ
  - 索引メタ (既存 `repos` テーブル): チャンク数、最終索引時刻
- **検証ルール**:
  - 検索系ツールの repo は `repos[].name` に存在必須。不在なら利用可能 repo 一覧を添えたエラー (FR-004)
  - 通常運用の `repos[]` に評価台 (fastapi) を含めない (FR-008)
- **関係**: `chunks.repo` / `chunks_fts.repo` / `edges.repo` が `repo.name` を参照 (既存 FK 相当、論理参照)。

### repo 宣言 (接続ラベル)

クライアント接続が「自分の既定 repo」を表明する情報。**永続エンティティではなく接続コンテキスト上の値**。

- **保持場所**: 第一経路 = SSE URL クエリ `?repo=` (stdio 単一クライアント時のみ `env CODE_RAG_REPO`)。第一経路が成立しない場合の縮退 (FR-015) = (B1) repo 毎の分離 SSE エンドポイント (マウントパスで repo 一意化) / (B2) 接続後の `use_repo` 宣言ツール (サーバ側セッション保持)。いずれも DB には保存しない。暗黙 `default` 退避は禁止。
- **フィールド**: `declared_repo` (string, 接続ごとに固定)
- **検証ルール**:
  - 空文字・空白のみ → 未宣言扱い (Edge Case)
  - 検索時の解決順: 明示引数 > `declared_repo` > エラー (FR-003、暗黙 default 廃止)

### 取り込みスナップショット

作業機のプロジェクトをサーバ側ローカルへ差分転送した複製。索引の入力源。

- **保持場所**: サーバ側ファイルシステム `data/snapshots/<repo>/` (git 管理外、`0700` 権限で所有者限定 / FR-014)。DB エンティティではない。平文複製のため秘密が索引外でもディスクに残る前提で権限保護する。
- **変更検知単位**: ファイル (rsync の差分判定 + indexer の mtime/hash による差分インデックス)
- **状態遷移**:
  - 未取り込み → (rsync) → スナップショット作成 → (index_repo) → 索引出現
  - 取り込み済 + 一部変更 → (rsync 差分) → 変更ファイルのみ更新 → (差分 index) → 変更チャンクのみ再計算
  - ファイル削除 → (rsync `--delete`) → スナップショットから消去 → (差分 index) → 該当チャンク + edges 削除 (FR-006/Acceptance 3)
- **検証ルール (段の責務分担、FR-009)**: rsync 段は**パス/パターン + サイズ除外のみ** (`.git`/`node_modules`/生成物/`.gitignore`/`.env` 等パターン、巨大ファイルは `--max-size`)。**秘密情報の内容ベース検知は hygiene 段 (索引除外) が担う** — rsync は内容を走査しないため「二重」は段の責務分担を指し、転送段の内容検査ではない。

### 評価資産

品質計測用のコーパス + 質問セット + 過去結果。通常運用 repo から外れるが保全。

- **保持場所**: コーパス = `/path/to/code-rag-targets/fastapi` (温存)、質問セット = `eval/qa.yaml`、過去結果 = `eval/results/`。
- **検証ルール**: 通常の `repos[]`・repo 一覧に現れない (SC-006)。eval 実行時のみ評価用設定で対象化。物理削除禁止。

## DB schema への影響 (既存テーブル)

```text
chunks      (id, repo, path, symbol, lineno_start, lineno_end, code, ...)  -- repo 列 既存、変更なし
chunks_fts  (chunk_id, repo UNINDEXED, code)                                -- repo スコープ済、変更なし
repos       (name, ...索引メタ)                                              -- 既存、変更なし
edges       (id, repo, src, dst, path, ...)                                 -- repo 列 既存、変更なし
idx_chunks_repo / idx_chunks_repo_path / idx_edges_repo_*                    -- 既存インデックス、流用
```

**結論**: スキーマ変更は不要。マルチ repo の物理基盤は 001/002 で既に整備済。本機能は「repo を default 固定で使っていた箇所を、接続宣言 + 解決順で動的にする」アプリ層の変更と、取り込み経路・同期トリガの追加に集約される。
