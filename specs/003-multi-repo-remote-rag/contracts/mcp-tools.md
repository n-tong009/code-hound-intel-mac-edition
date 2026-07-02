# Contract: MCP ツール (repo スコープ対応)

既存 MCP ツールの repo 引数挙動を「明示 > 接続宣言 > エラー」へ変更し、repo 一覧ツールを追加する。**ランキング・返却フォーマットは不変** (SC-007)。

## 共通: repo 解決

全 repo スコープ系ツールで共通: `search_code` / `search_code_debug` / `grep_code` / `find_references` / `related_code` / **`get_file_range`** / **`list_symbols`**。

> いずれも実コード上 `repo="default"` 既定 (server.py)。default repo 除去に伴い全て解決対象 (C2: `list_symbols` 欠落 / C3: `get_file_range` 漏れを是正)。

- 引数 `repo` の既定値を `"default"` から **未指定 (None)** へ変更
- 解決順:
  1. `repo` 引数が非空 → それを使う (明示指定優先)
  2. 接続宣言が非空 → それを使う
  3. どちらも無い → **エラー** (暗黙 default フォールバック禁止)
- repo が `config.repos[].name` に不在 → エラー

### 接続宣言の経路 (C1: SSE では env 不達)

本構成は `transport: sse` + 単一プロセス複数クライアント共有。**クライアント MCP 設定の env はサーバへ届かない** (サーバ `os.environ` はサーバ機自身、接続別に持てない)。

- **SSE (第一経路)**: 接続別宣言は **SSE URL クエリ `?repo=<name>`**
- **stdio** (単一クライアント): `CODE_RAG_REPO` env も可
- fastmcp が SSE 接続の URL クエリをツールへ渡せるか実装前 PoC 検証 (tasks T006a)
- **縮退経路 (FR-015、第一経路が落ちた場合)**: 暗黙 `default` へ退避せず以下へ切替。**いずれも『毎回 repo 指定不要』を維持**:
  - **(B1)** repo 毎に分離した SSE エンドポイントを mount し、マウントパスから repo を一意化 (クライアントは自分の repo の URL に接続)
  - **(B2)** 接続後に明示宣言する MCP ツール `use_repo(name)` でサーバ側セッションへ保持
  - T006a は第一経路の可否に加え不可時の B1/B2 採択まで決める

### エラー応答フォーマット

```json
{
  "error": "repo_not_specified",
  "message": "repo を指定してください。接続で CODE_RAG_REPO を宣言するか、repo 引数を渡してください。",
  "available_repos": ["market_brief", "..."]
}
```

```json
{
  "error": "repo_not_found",
  "message": "repo 'xxx' は存在しません。",
  "available_repos": ["market_brief", "..."]
}
```

- 空文字・空白のみの repo / 宣言 → 「未指定」と同じ扱い (`repo_not_specified`)

## ツール: `list_repos` (新規)

通常運用で検索可能な repo 一覧を返す。

- **入力**: なし
- **出力**:
  ```json
  [
    {"name": "market_brief", "languages": ["python"], "chunks": 1234, "last_indexed": "2026-06-19T17:00:00Z"}
  ]
  ```
- 評価台 (fastapi) は含めない (SC-006)

## ツール: `search_code` / `search_code_debug` (変更)

- シグネチャ: `repo` 既定を None へ。それ以外の引数・返却は不変
- 解決順 + エラー応答は上記共通に従う
- ランキング (vector + FTS5 BM25 + RRF + rerank + diversity) は一切変更しない (FR-011)

## ツール: `grep_code` / `find_references` / `related_code` / `get_file_range` / `list_symbols` (変更)

- 同じく `repo` 既定を None へ、共通解決順を適用
- 既存の `repo_not_found` 返却を共通フォーマットへ統一
- `get_file_range` (server.py:235) / `list_symbols` (server.py:408) も `repo="default"` 既定のため対象に含む

## 受け入れ基準との対応

- FR-002/FR-003 → 解決順
- FR-004 → `repo_not_found` + available_repos
- US1 Acceptance 1-4 → 上記全分岐
- SC-001/SC-002 → repo スコープで他 repo 混入 0%、未指定は 100% エラー
