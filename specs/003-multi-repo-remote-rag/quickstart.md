# Quickstart / Validation: マルチ repo リモート RAG

本機能が end-to-end で動くことを示す検証手順。詳細は [contracts/](contracts/) と [data-model.md](data-model.md) を参照。

## 前提

- RAG サーバ機 Mac A 常時稼働、`server` launchd 起動済 (127.0.0.1:8765 SSE)
- 作業機 Mac B から Mac A への SSH トンネル確立 (ローカルポートフォワード、既存 001/002 手順踏襲)
- Intel Mac ピン環境 (`onnxruntime==1.17.3` / `numpy<2.0`)

### 確定した実運用構成 (end-to-end 稼働確認済)

Dev Container 経由でクライアントを運用する場合の接続経路:

```
[Dev Container]                  [Mac B 作業機]                    [Mac A = RAG専用機]
 Claude Code + code-rag MCP      fswatch 常駐 watcher              code-rag server (127.0.0.1:8765 SSE)
 編集 → bind mount →             <作業機ソースルート>/<repo>        code-rag ルート
 host.docker.internal:8765 SSE   └ 2秒デバウンス → sync_repo.sh    data/snapshots/<repo>/ (0700)
   で code-rag に接続              ──rsync(SSH)──→ snapshot → 差分index
```

- コンテナは `host.docker.internal:8765` (= Mac B) の SSE に接続する。Mac B は SSH ローカルポートフォワードで 8765 を Mac A の 127.0.0.1:8765 へトンネルする (Mac A の server は 127.0.0.1 bind のみ / Constitution III)。コンテナから届かせるため、トンネルは Mac B の全 interface で bind する (`ssh -N -L 0.0.0.0:8765:127.0.0.1:8765 <MacA>`)。
- Mac B の常駐は launchd 2本: (1) SSH トンネル維持 (RunAtLoad/KeepAlive)、(2) `scripts/watch_and_sync.sh` 維持 (RunAtLoad/KeepAlive)。ログイン時自動起動。
- Mac A の常駐は launchd 1本: code-rag server (`server.py --transport sse`)。
- 接続宣言: コンテナの `.mcp.json` で SSE 接続に `X-Repo` ヘッダを設定 (T006a 確定経路)。
- 初回登録は手動フル同期 `sync_repo.sh <repo> <src>` を1回実行 (auto-register で `config.yaml` に追記される)。以後は watcher が差分同期を担う。

## シナリオ 1: repo スコープ (US1 / SC-001・SC-002)

```bash
# config.yaml に 2 repo 登録 (market_brief / 別 repo)、両方インデックス済の状態で
# クライアント A は SSE 接続に X-Repo ヘッダで宣言:
#   .mcp.json → "headers": { "X-Repo": "market_brief" }   (SSE 第一経路 / T006a 確定)
#   stdio 運用時のみ env CODE_RAG_REPO=market_brief も可
```

- repo 引数を省略して検索 → market_brief のヒットのみ (他 repo 混入 0%)
- repo 引数で別 repo を明示 → 明示が優先される
- 宣言も明示も無いクライアントで検索 → `repo_not_specified` エラー + available_repos (暗黙 default 無し)
- 存在しない repo 指定 → `repo_not_found` エラー + available_repos

**期待**: SC-001 混入率 0%、SC-002 未指定は 100% エラー。

## シナリオ 2: リモート取り込み + 差分インデックス (US2 / SC-003・SC-004)

```bash
# 作業機 Mac B 上で
scripts/sync_repo.sh market_brief ~/work/Market_Brief
```

- 初回: スナップショット作成 → インデックス → `list_repos` に market_brief 出現 → 検索ヒット
- 変更ゼロで再実行 → rsync 転送ゼロ → 差分 index が全 chunk の hash 一致を検知 → 再 embed ゼロ → 即座に返る (SC-004)
- ファイル削除して再実行 → 該当チャンク + edges が索引から消える

**期待**: SC-003 手作業のファイル配置なしで取り込み + インデックス完了。

> **判定閾値 (C8)**: SC-004「待ち時間ほぼゼロ」は定性のため目安を置く。**変更ゼロ同期の追加待ち時間 < 数秒** (rsync の stat 走査 + hash 照合のみ) を合格基準とする。待ち時間は repo サイズ (ファイル数) に依存し、大規模 repo では rsync の全 stat 走査が支配的になる点に注意。

## シナリオ 3: 検索起点の鮮度 (US3 / SC-005)

```bash
# Mac B で watch_and_sync.sh を launchd 常駐させた状態で
# コンテナ (bind mount 経由で Mac B のソースが変わる) でファイルを編集 → search_code を実行
```

- Mac B の `scripts/watch_and_sync.sh` が fswatch でソースディレクトリを監視 (2秒デバウンス)
- コンテナ内でファイルを編集 → bind mount 経由で Mac B のソースに反映 → fswatch が検知 → `sync_repo.sh` が起動 → rsync 差分転送 → Mac A で差分 index → 直後の検索に反映される
- 編集せず連続検索 → watcher は発火せず、後続の同期・検索は待たされず即座に返る (発火しても差分 index が hash 一致で再 embed ゼロ / 変更ゼロ追加待ち < 数秒 / C8)

> pre-tool-use フック方式 (`hooks/client/`) は Dev Container 運用ではコンテナ内 (Linux) から Mac 側の `sync_repo.sh`・ソースパスへ到達できず不採用。作業機がホスト直運用 (コンテナ非経由) の場合の代替経路として `hooks/client/` に温存する。

**期待**: SC-005 編集が後続検索に反映。フック自体は作業機 Claude Code 上で動くため pytest 不能 → 本シナリオは手動検証。サーバ側機構 (差分 index・削除反映・直列化) は `test_indexer_delete.py` / `test_sync_serialize.py` で自動被覆済。

## シナリオ 4: 評価台移行 (US4 / SC-006)

```bash
# config.yaml の repos から fastapi/default を除外後
```

- `list_repos` / 通常検索に fastapi が現れない
- 評価資産 (`/Users/.../code-rag-targets/fastapi` + `eval/qa.yaml` + `eval/results/`) は温存
- 評価用設定で `uv run python eval/run.py` → recall@5 再計測可能

**期待**: SC-006 通常運用に評価台が出ず、かつ再計測可能。

## 品質非介入の確認 (SC-007 / Constitution I)

```bash
# 移行前後で同一 repo・同一クエリの検索ランキングを比較
uv run python eval/run.py   # recall@5 ≥ 0.92 維持を確認
```

**期待**: ランキング不変。recall@5 が移行前後で同値。**実測 (2026-06-20、移行後)**: recall@5 = 0.95 (19/20)、ベースライン同値 (`eval/results/spec003_postmigration_*`)。

## 直列化・除外の確認 (FR-012 / FR-009)

- 同一 repo へ同期を二重起動 → ロックで直列化、索引非破損 (`test_sync_serialize.py`)
- `node_modules`・巨大ファイル・秘密情報・`.gitignore` 対象がスナップショット/索引に入らない (`test_hygiene_multi.py`)
