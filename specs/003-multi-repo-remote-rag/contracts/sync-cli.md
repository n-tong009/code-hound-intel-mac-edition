# Contract: 取り込み同期 CLI (`scripts/sync_repo.sh`)

作業機 (Mac B) のプロジェクトを RAG サーバ (Mac A) のスナップショットへ差分転送し、差分インデックスをトリガする。SSH トンネル経由・片方向 (作業機 → サーバ)。

## 呼び出し

```bash
scripts/sync_repo.sh <repo_name> <src_path>
# 例: scripts/sync_repo.sh market_brief ~/work/Market_Brief
```

- `<repo_name>`: config の repo 名。サーバ側保管先 `data/snapshots/<repo_name>/` を決める
- `<src_path>`: 作業機上のプロジェクトルート

## 振る舞い

1. **rsync 差分転送** (作業機 → サーバ `data/snapshots/<repo_name>/`)
   - `--delete` で削除を反映 (FR-006 削除ケース)
   - 除外は**パス/パターン + サイズのみ** (FR-009 転送段の責務): `.git`/`node_modules`/生成物/`.gitignore`/`.env` 等パターン + `--max-size` で巨大ファイル。**秘密情報の内容ベース検知は索引段 (hygiene) が担う** — rsync は内容を走査しない。「二重」は段の責務分担で、転送段の内容検査ではない
   - SSH トンネル経由 (127.0.0.1 のローカルポートフォワード、FR-013)
   - サーバ側保管先 `data/snapshots/<repo_name>/` は `0700` 権限で作成・維持 (平文複製の閲覧限定 / FR-014・Principle III)
2. **差分インデックス起動** (サーバ側): `indexer.py --repo <repo_name>`
   - **前提 (C7)**: `index_repo` の差分モード (tasks T007a)。フル走査で削除反映しつつ `content_hash` 一致 chunk は embed スキップ。**現状のフル `--repo` は全 chunk 削除 + 全再 embed で FR-006 違反のため、T007a の実装が前提**
   - 変更/削除ファイルのみ再処理、未変更は再計算しない (FR-006)
   - 同一 repo の同期/インデックスは直列化 (repo 単位ロック、FR-012)
3. **変更ゼロ時**: rsync が転送ゼロを検知 → 再インデックスを呼んでも差分モードが全 chunk の hash 一致を検知 → 再 embed ゼロ → 即座に返る (SC-004)

## 終了コードと冪等性

- 0: 同期 + インデックス成功 (変更ゼロ含む)
- 非0: 転送失敗 / トンネル不通 (オフライン)。サーバ既存索引で検索は継続可 (Edge Case)
- 冪等: 連続実行で変更ゼロなら no-op。多重起動時はロックで直列化 (索引非破損)

## クライアントフック契約 (鮮度自動化、US3)

- 作業機側 Claude Code の **pre-tool-use フック**が、検索ツール実行前に `sync_repo.sh <repo> <src>` を同期実行 (完了待ち) → その後に検索
- フックは本リポジトリでは手順書 + サンプルとして提供 (`hooks/client/`。既存 `hooks/post-commit` は git hook で役割分離 / C5)。サーバはフック有無に依存しない
- 接続宣言は SSE URL `?repo=` で行う (env 不達 / C1)。第一経路が落ちた場合は縮退 (B1 分離エンドポイント / B2 `use_repo` ツール、FR-015)
- 変更ゼロ時はフックが即座に返り、検索の追加待ち時間が無視できる (SC-004/SC-005)
