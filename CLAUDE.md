# code-rag (CodeHound Intel Edition)

Intel Mac 常駐のコード意味検索 MCP サーバ。Constitution: `.specify/memory/constitution.md` (v1.0.1) が全プラクティスに優先。

## 不変条件

- `onnxruntime==1.17.3` / `numpy<2.0` ピン厳守 (Intel Mac。numpy 2 で即死)
- recall@5 ≥ 0.92 (`uv run python eval/run.py`)。検索品質(ランキング)に触る変更は計測なしにマージ禁止。引数なし `eval/run.py` は評価台 fastapi (DB repo "default") を計測する不変条件
- server は 127.0.0.1 bind のみ。リモートは SSH トンネル
- chunking.py の挙動変更注意 — chunk 数が変わると eval 比較が壊れる

## マルチ repo 運用 (specs/003)

- 通常運用 repo は `config.yaml` の `repos[]`。評価台 fastapi は外し `eval/config.eval.yaml` へ分離 (FR-008)
- 接続別 repo 宣言: **SSE = `X-Repo` HTTP ヘッダ** (URL `?repo=` は fastmcp で不可)、stdio = `CODE_RAG_REPO` env。解決順は 明示引数 > 接続宣言 > エラー (暗黙 default 廃止)
- 取り込み: `scripts/sync_repo.sh <repo> <src>` で作業機 → `data/snapshots/<repo>/` へ rsync (0700) + 差分索引。検索契機の自動同期は `hooks/client/` (任意)
- 差分索引: `indexer.py` は `content_hash` 一致 chunk の再 embed をスキップ、削除ファイルの chunk/edges を除去 (FR-006/SC-004)

## 開発フロー

- Spec Kit: constitution → specify → plan → tasks → implement
- サブエージェント: 調査=code-scout / 実装=code-writer / テスト=tester / レビュー=reviewer / 依存監査=security-auditor
- フェーズ毎に git コミット。稼働中 launchd サービスは eval 通過後にのみ再起動

<!-- SPECKIT START -->
Active feature: `specs/003-multi-repo-remote-rag/`
Current plan: [specs/003-multi-repo-remote-rag/plan.md](specs/003-multi-repo-remote-rag/plan.md)
(research / data-model / contracts / quickstart も同ディレクトリ。完了済: specs/001-hound-intel-port/, specs/002-code-graph-search/)
<!-- SPECKIT END -->
