# Phase 0 Research: 複数 dev コンテナ共有・汎用マルチ repo RAG サーバ化

spec の Assumptions「環境固有値は計画/実装フェーズで確定する」を解消し、技術選択の根拠を残す。

## R1. 接続単位の repo 宣言 (どう「自分の repo」を表明するか)

- **Decision**: クライアントの MCP サーバ設定 (`env` または SSE URL クエリ) で `CODE_RAG_REPO=<name>` を 1 度宣言。サーバは接続コンテキストから読み、検索ツールで repo 引数が省略された時の既定にする。解決順は **明示引数 > 接続宣言 > エラー**。暗黙 `default` フォールバックは廃止。
- **Rationale**: 各 dev コンテナは自分の MCP 設定ファイルを持つので、接続単位の宣言が最も自然で取り違えが起きない。fastmcp SSE は接続ごとに env/header を保持できる。検索ごとの repo 明示を不要にしつつ、明示指定で上書き可能 (Acceptance Scenario 2)。
- **Alternatives**: (a) 毎回 repo 必須 → 利便性を損ね US1 の価値を削ぐ。(b) サーバ側で 1 クライアント 1 repo 固定マップ → クライアント増減のたびにサーバ設定変更が必要で汎用性に欠ける。
- **T006a PoC 結論 (2026-06-20、fastmcp 3.2.4 実 E2E 検証で確定)**:
  - **`?repo=` URL クエリは不可**。初回 `GET /sse?repo=foo` でストリーム確立後、ツール呼出は別の `POST /messages/?session_id=...` で来る。ツール内 `get_http_request().query_params` は `{session_id}` のみで `repo` が消える。
  - **HTTP ヘッダは可**。クライアント `SSETransport(url, headers={"X-Repo": ...})` のヘッダは毎ツール呼出の POST にも伝播。サーバは `from fastmcp.server.dependencies import get_http_headers` → `get_http_headers().get("x-repo")` で接続別に読める。
  - **決定**: 接続別 repo 宣言は **`X-Repo` HTTP ヘッダ**方式を採択。`?repo=` クエリは不採用。stdio 単一クライアント時のみ `CODE_RAG_REPO` env。Claude Code の MCP SSE 設定は `headers` フィールドでヘッダ付与可。
- **不採用となった縮退案 (B1/B2)**: ヘッダ方式が成立したため未採用だが記録として残す。(B1) repo 毎の分離 SSE エンドポイント (マウントパスで repo 一意化) / (B2) 接続後の `use_repo(name)` 宣言ツール (サーバ側セッション保持)。いずれもヘッダ方式より複雑。

## R2. リモート作業機 → サーバの取り込み経路

- **Decision**: 作業機 (Mac B) から RAG サーバ (Mac A) へ **rsync over SSH トンネル** で片方向 (作業機 → サーバ) 差分転送。サーバ側保管場所は `data/snapshots/<repo>/` (git 管理外、既存 `data/` 配下に統一)。`scripts/sync_repo.sh <repo> <src>` が rsync を実行し、完了後にサーバの差分インデックス (`indexer.py --repo <name>`) をトリガ。
- **Rationale**: rsync は差分転送・削除反映 (`--delete`) を標準で持ち、変更ゼロならほぼ無負荷 (FR-006/SC-004)。新規依存ゼロ (Constitution II)。転送方向を既存トンネル方向 (作業機 → サーバ) と揃える (spec Assumptions)。保管を `data/` 配下に置くことで既存の hygiene/indexer のパス前提を流用。
- **Alternatives**: (a) git push/pull → コミット前の作業ツリーを反映できず鮮度要件 (US3) に反する。(b) NFS/SMB マウント → ネットワークファイルシステムは Local-First セキュリティ (III) とトンネル前提を崩す。(c) サーバが作業機へ pull (逆方向 SSH) → トンネル方向が逆になり 127.0.0.1 bind の前提と非整合。
- **rsync 除外 (段の責務分担、FR-009)**: rsync 段は**パス/パターン除外のみ** — `.git`/`node_modules`/生成物/`.gitignore`/`.env` 等のパターン + `--max-size` で巨大ファイル。**秘密情報の内容ベース検知は索引段 (hygiene) が担う** (rsync は内容を走査しない)。「二重」とは段の責務分担であり転送段の内容検査ではない。
- **スナップショット保護 (FR-014、Principle III)**: `data/snapshots/<repo>/` は作業機コードの平文複製。秘密が索引に入らずともディスクに着地し得るため、ディレクトリを `0700` 権限 (所有者限定) + git 管理外で保護。`sync_repo.sh`/作成時に権限を設定。

## R3. 検索起点の自動同期 (厳密鮮度)

- **Decision**: クライアント (作業機側 Claude Code) の **pre-tool-use フック**で、検索ツール実行前に `sync_repo.sh` を起動 → 差分インデックス完了 → 検索。変更ゼロなら rsync が即座に返り再インデックスを省略。サーバはフック無しでも手動同期 + 検索で動作 (フック非依存)。
- **Rationale**: 「検索アクセスを契機に最新反映」(FR-007) をクライアント側で実現。サーバを変更せずクライアント設定だけで鮮度を上げられる。変更ゼロ時は rsync の差分検知で待ち時間ほぼゼロ (SC-004)。
- **Alternatives**: (a) サーバ側 watcher (watchdog) のみ → 作業機のローカル編集をサーバは監視できない (別マシン)。watcher はサーバ側保管ディレクトリの変更監視には使えるが、転送契機が要る。(b) 定期 cron 同期 → 鮮度が同期間隔に律速され「編集が必ず反映」(SC-005) を満たさない。
- **直列化**: 同一 repo への同期/インデックス並行実行で索引破損を防ぐため、repo 単位のロック (ファイルロック or サーバ内ロック) で直列化 (FR-012)。

## R4. 評価台 (fastapi) の降格と評価資産保全

- **Decision**: `config.yaml` の `repos` から `default`/fastapi を除外し、通常の検索・repo 一覧から消す。評価資産 (`/path/to/code-rag-targets/fastapi` コーパス + `eval/qa.yaml` + `eval/results/`) は物理削除せず温存。eval 実行時のみ評価用 config (別ファイル or `--repo` 明示) で fastapi を対象にする。Constitution の「評価対象リポジトリ」記述を本移行に合わせ改定。
- **Rationale**: spec FR-008/SC-006 と Assumptions に直結。品質ゲート (Principle I) は「ランキングに触る変更時にのみ再計測」へ読み替え、評価資産を保全することで再計測可能性を担保。
- **Alternatives**: 評価台を通常 repo に残す → SC-006 (一覧に現れない) に反する。評価資産を削除 → 再計測不能で Principle I を破る。

## R5. 環境固有値の確定

- **取り込み対象 (初期)**: Market_Brief プロジェクト 1 件。作業機 Mac B 上の作業ツリー。
- **対象言語**: 実装時に Market_Brief の構成を確認して確定 (python/typescript 等)。config の `languages` に設定。
- **サーバ側保管場所**: `data/snapshots/market_brief/`。
- **repo 名**: `market_brief` (config の name)。
- **接続ホスト名 / トンネル**: 作業機 → Mac A への SSH トンネル設定。具体ホスト名・ポートは手順書 (quickstart) で確定。既存 001/002 の SSH トンネル手順を踏襲。
- **未確定で残すもの**: Market_Brief の正確なローカルパス・言語構成・Mac A のホスト名は実装/設定フェーズで埋める (本リポジトリのコードは環境非依存に保ち、値は config.yaml と手順書に外出し)。

## まとめ (全 NEEDS CLARIFICATION の解決状況)

- 接続宣言の仕組み → R1 で決定 (env/クエリ、解決順)
- 取り込み経路 → R2 で決定 (rsync over SSH、`data/snapshots/`)
- 鮮度の自動化 → R3 で決定 (クライアント pre-tool フック + 直列化)
- 評価台移行 → R4 で決定 (config 除外 + 資産保全 + Constitution 改定)
- 環境固有値 → R5 で外出し (config.yaml + 手順書)。残る具体値は実装/設定時に確定し、コードは非依存に保つ
