# Feature Specification: CodeHound Intel Edition — code-rag の code-hound 良所取り進化

**Feature Branch**: `001-hound-intel-port`

**Created**: 2026-06-12

**Status**: Draft

**Input**: User description: "code-hound (別マシンで開発したほぼ完成系のコード意味検索 MCP サーバ) のアーキテクチャを、Intel Mac (x86_64) で稼働中の code-rag に取り込み、この PC を RAG 専用機にする。embedding は fastembed、アクセスは 127.0.0.1 + SSH トンネル、code-rag を in-place 進化させる。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 検索品質を保ったままストレージ・embedding を刷新 (Priority: P1)

利用者 (MacBook 上の Claude Code) は、これまでと同じ MCP ツール (`search_code` 等) で同等以上の品質の検索結果を得られる。内部はベンダーピン問題のない SQLite + fastembed に置き換わっているが、利用者からは見えない。

**Why this priority**: 検索品質がプロダクトそのもの (Constitution I)。品質を落とす移行は移行ではなく退行。

**Independent Test**: `eval/run.py` を新実装で実行し recall@5 ≥ 0.92 を確認できる。

**Acceptance Scenarios**:

1. **Given** fastapi リポジトリがインデックス済み、**When** eval/qa.yaml の 20 問を実行、**Then** recall@5 ≥ 0.92 (目標 0.95 維持)
2. **Given** 新ストレージ、**When** `search_code("認証処理")` を実行、**Then** BM25 + vector の RRF 融合結果が suggested_ranges 付きで返る
3. **Given** Ollama が停止している状態、**When** インデックス・検索を実行、**Then** 外部プロセス依存なしに完了する (fastembed はインプロセス)

---

### User Story 2 - ループバック強制とトンネル経由アクセス (Priority: P2)

サーバーは 127.0.0.1 にのみ bind し、MacBook からは SSH トンネル経由で利用する。誤って LAN に露出する設定では起動を拒否する。

**Why this priority**: Constitution III。現行の 0.0.0.0 bind は LAN 全体に無認証で露出しており、移行で必ず塞ぐ。

**Independent Test**: `server.host: 0.0.0.0` の config で起動 → エラー終了を確認。`ssh -L` トンネル経由で MCP 接続成功を確認。

**Acceptance Scenarios**:

1. **Given** config の host が `0.0.0.0`、**When** サーバー起動、**Then** 環境変数オプトインなしでは起動拒否しエラーメッセージを表示
2. **Given** host が `127.0.0.1`、**When** MacBook から `ssh -L 8765:localhost:8765 <mini>` 経由で接続、**Then** MCP ツールが正常応答
3. **Given** 新しい CONNECT.md、**When** MacBook 側のセットアップ手順に従う、**Then** DHCP IP 変動の影響を受けない接続が確立する (SSH ホスト名/IP の管理方法を含む)

---

### User Story 3 - テスト・CI 基盤の獲得 (Priority: P3)

開発者は pytest でリグレッションを検知できる。code-hound のテスト思想 (security / retrieval metadata / observe metrics) を移植し、今後の変更の安全網にする。

**Why this priority**: 今後の Phase 改修の土台。ただし P1/P2 が動かなければ価値ゼロなので P3。

**Independent Test**: `uv run pytest` が green。

**Acceptance Scenarios**:

1. **Given** 移植済みテストスイート、**When** `uv run pytest`、**Then** 全テスト pass
2. **Given** security.py のテスト、**When** loopback 判定・unsafe bind 拒否のケースを実行、**Then** 仕様どおりの挙動を検証できる

---

### User Story 4 - RAG 専用機としての常時運用 (Priority: P3)

サーバー機は電源を入れておけば、launchd でサーバーと watcher が自動稼働し、対象リポジトリの変更が自動で差分インデックスされる。

**Why this priority**: 既存の launchd 運用が動いており、移行後の再設定が必要。

**Acceptance Scenarios**:

1. **Given** 新実装デプロイ済み、**When** launchd サービス再起動、**Then** server (port 8765, loopback) と watcher が稼働
2. **Given** 対象リポジトリのファイル変更、**When** watcher が検知、**Then** 該当ファイルのみ再インデックス (content_hash 差分)

---

### Edge Cases

- fastembed のモデルキャッシュが未ダウンロードの初回起動 → 起動が遅延しても完了する (HF Hub 接続が必要な旨をログに出す)
- numpy 2 系が誤って入った場合 → `onnxruntime` import 時に即死。pyproject のピンで防止し、起動時バージョンチェックで早期検知
- 既存 lance.db からの移行 → データ移行はしない。新ストレージで全リポジトリを再インデックス (embedding モデルが変わるため必須)
- 旧スキーマ/旧 DB ファイルの残骸 → `data/lance.db*` は退避済み資産として残置、新 DB は別ファイル名
- SSH トンネル切断時 → MacBook 側の再接続手順を CONNECT.md に明記

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST ストレージを SQLite 単一ファイル + numpy ベクタ (BLOB) に置き換え、lancedb 依存を除去する
- **FR-002**: System MUST embedding を fastembed `BAAI/bge-small-en-v1.5` (dim=384) で行い、Ollama 依存を除去する
- **FR-003**: System MUST 既存 MCP ツール群 (`search_code`, `grep_code`, `find_references`, `get_file_range`, `list_repos`, `stats`, reindex) の互換 API を維持する
- **FR-004**: System MUST AST header chunking (tree-sitter symbol 抽出) を維持する — code-hound の行ベース簡易版に退行させない
- **FR-005**: System MUST `max_file_bytes` 256KB、diversity cap (`max_per_file: 2`)、fastapi 用 `extra_exclude_dirs` を維持する
- **FR-006**: System MUST 127.0.0.1 以外への bind を環境変数オプトインなしで拒否する (code-hound security.py 移植)
- **FR-007**: System MUST 検索結果に `untrusted_repository_content` マークを付与する (prompt injection 対策)
- **FR-008**: System MUST クエリログ (JSONL、パスのみ)・秘密スキャン・`.gitignore` 尊重を継続する
- **FR-009**: System MUST watcher による content_hash 差分インデックスと BM25 キャッシュ無効化を新ストレージで動作させる
- **FR-010**: System MUST `eval/run.py` を新実装で動作させ、recall@5 を計測可能に保つ
- **FR-011**: System MUST pytest テストスイート (security / retrieval / observe) を備える
- **FR-012**: System MUST launchd plist (server / watcher) を新構成で更新する
- **FR-013**: CONNECT.md MUST SSH トンネル方式の接続手順に書き換える
- **FR-014**: pyproject MUST `onnxruntime==1.17.3` / `numpy<2.0` をピンし、lancedb / llama-index / httpx / pandas / flashrank の不要依存を整理する

### Key Entities

- **chunk**: コード断片。id, repo, path, symbol, lineno_start/end, language, code, content_hash, indexed_at, file_mtime, commit_sha, last_modified, last_author, vector(384) — Phase 3 スキーマを SQLite で継承
- **repo**: 検索対象リポジトリ。config.yaml の `repos` で宣言、チャンクは repo カラムでフィルタ
- **query log**: ツール呼出記録。timestamp, query_id, tool, args, returned_paths, latency

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: recall@5 ≥ 0.92 (eval/qa.yaml 20 問、目標: 現行 0.95 維持)
- **SC-002**: インデックス処理 (fastapi リポ全体) が外部プロセス (Ollama) なしで完走する
- **SC-003**: `uv run pytest` が全件 pass
- **SC-004**: LAN の他ホストから直接ポート 8765 に到達できない (loopback bind 検証)
- **SC-005**: MacBook から SSH トンネル経由で検索が完了する
- **SC-006**: 検索レイテンシが現行 (Ollama embed HTTP 経由) と同等以下

## Assumptions

- 既存 lance.db のデータ移行は不要 — embedding モデル変更のため全再インデックスが前提
- Python は 3.12 に更新する (uv が Intel x86_64 向け CPython を取得可能なことは確認済)
- code-hound のコード (`~/code-hound-main`) は参照可能で、ライセンス上の制約なく流用できる (同一作者)
- 評価対象リポジトリは引き続き fastapi のみ。マルチリポ拡張は本フィーチャーの範囲外
- bearer token 認証は導入しない (SSH トンネルが認証を兼ねる)。将来 LAN 直公開する場合に再検討
- 稼働中の launchd サービスは実装完了・eval 通過まで現行版のまま動かし続ける
