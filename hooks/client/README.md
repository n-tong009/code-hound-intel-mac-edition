# クライアント側セットアップ (作業機 Mac B)

specs/003 US3 (検索時に最新コードが自動反映) を作業機側で実現する手順。
サーバ (Mac A) はこのフックの有無に依存しない — フック未導入でも手動 `sync_repo.sh` + 検索で動作する (FR-007)。フックはその同期を検索契機で自動化するだけ (FR-007a = SHOULD)。

## 1. SSH トンネル確立 (127.0.0.1 ローカルフォワード / FR-013)

サーバは 127.0.0.1 bind のみ (Constitution III)。作業機からはトンネル経由で到達する。

```bash
# MCP (SSE) 用ポート + 取り込み rsync 用 SSH を確保
ssh -N -L 8765:localhost:8765 <user>@<mac-a-host>
```

具体ホスト名・ポートは環境固有 (research R5)。001/002 のトンネル手順を踏襲。

## 2. 接続宣言 — repo を `X-Repo` HTTP ヘッダで宣言 (T006a 確定)

> **重要**: SSE の URL クエリ `?repo=` は **不可**。fastmcp 3.2.4 では SSE ストリームは
> 初回 `GET /sse?...` で確立し、ツール呼出は別の `POST /messages/?session_id=...` で来るため、
> URL クエリは消える。**接続別 repo 宣言は HTTP ヘッダ `X-Repo` で行う** (PoC 検証済)。

作業機の Claude Code MCP 設定 (`.mcp.json` 等) で SSE サーバにヘッダを付与:

```json
{
  "mcpServers": {
    "code-rag": {
      "type": "sse",
      "url": "http://127.0.0.1:8765/sse",
      "headers": { "X-Repo": "market_brief" }
    }
  }
}
```

これで検索ツールに毎回 `repo` を渡さなくても、この接続は `market_brief` にスコープされる。
個別呼出で `repo` 引数を明示すればそちらが優先される (解決順: 明示引数 > 接続宣言 > エラー)。
stdio 単一クライアント運用時のみ `CODE_RAG_REPO` env でも宣言可。

## 3. 検索契機の自動同期フック登録 (任意 / SHOULD)

`pre-search-sync.sh` を PreToolUse フックに登録すると、検索直前に作業ツリーをサーバへ同期する。

作業機側で環境変数を export:

```bash
export CODE_RAG_REPO=market_brief          # config.yaml repos[].name と一致
export CODE_RAG_SRC=~/work/Market_Brief     # 作業ツリーのローカルパス
export CODE_RAG_REMOTE=<user>@127.0.0.1     # トンネル先 (sync_repo.sh が rsync/ssh に使用)
export CODE_RAG_SSH_PORT=22                  # 必要に応じて
```

Claude Code settings.json (作業機):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "search_code|search_code_debug|grep_code|find_references|related_code",
        "hooks": [
          { "type": "command",
            "command": "/path/to/code-rag/hooks/client/pre-search-sync.sh" }
        ]
      }
    ]
  }
}
```

- 変更ゼロ時は rsync が即返り、差分インデックスも再 embed ゼロ → 追加待ち時間はほぼゼロ (SC-004)。
- 同期失敗 (オフライン等) でもフックは検索をブロックせず、サーバ既存索引で継続する (フェイルオープン)。
- フックを入れない場合は、編集後に手動で `scripts/sync_repo.sh market_brief ~/work/Market_Brief` を実行してから検索する (FR-007 手動経路)。

## 既存 git post-commit フックとの違い (C5)

`hooks/post-commit` は **コミット時** にサーバへ再インデックスを依頼する git フックで、役割が異なる。
本 `hooks/client/` は **検索時** の鮮度同期 (未コミットの作業ツリーを反映) を担う。両者は独立。
