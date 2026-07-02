# 接続元マシンの設定 (SSH トンネル方式)

サーバーは **127.0.0.1:8765 のみ** で待ち受ける (LAN へ直接公開せず loopback bind とする設計)。
127.0.0.1 bind は「サーバー機の中からしか繋げない」という意味なので、別のマシンから使うには通り道が要る。LAN へ公開する代わりに、SSH トンネルでその通り道を作る。以降はその手順。

ここで言う **サーバー機** = code-rag を常駐させているマシン、**接続元マシン** = そこへ繋ぎに行く手元のマシン。`<server-ip>` / `<user>` などの `<...>` は自分の環境の値に置き換える。

## 1. SSH 設定 (接続元マシン側、初回のみ)

`~/.ssh/config` にホストを登録:

```ssh-config
Host mini
    HostName <server-ip>           # サーバー機の LAN IP (mDNS なら <server-host>.local)
    User <user>                    # サーバー機のログインユーザー名
    # トンネル切断検知 (任意だが推奨)
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

`mini` は任意のエイリアス名 (好きに決めてよい)。以降のコマンドに出てくる `mini` も同じ名前で読み替える。
鍵認証を済ませておく (`ssh-copy-id mini` など)。`ssh mini` でパスワードなしログインできれば OK。

## 2. トンネルを張る

```bash
ssh -N -L 8765:localhost:8765 mini
```

- `-N`: リモートコマンドを実行しない (トンネル専用)
- `-L 8765:localhost:8765`: 接続元マシンの localhost:8765 → サーバー機の 127.0.0.1:8765

バックグラウンドで張る場合:

```bash
ssh -f -N -L 8765:localhost:8765 mini
# 切断: pkill -f "ssh -f -N -L 8765"
```

## 3. MCP サーバーへの接続

`.mcp.json` (または `~/.claude.json` の `mcpServers`) に追加。URL は **localhost** を指す。`X-Repo` ヘッダで接続レベルの repo を宣言する (検索ツールの `repo` 引数を省略できる):

```json
{
  "mcpServers": {
    "code-rag": {
      "type": "sse",
      "url": "http://localhost:8765/sse",
      "headers": {
        "X-Repo": "my-project"
      }
    }
  }
}
```

> **暗黙 default 廃止**: `X-Repo` ヘッダも検索ツールの `repo` 引数も無い場合は `repo_not_specified` エラーになる。stdio 接続の場合は `CODE_RAG_REPO` 環境変数で宣言。

## 4. 接続確認

接続元マシン側 (トンネルを張った状態で):

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8765/sse --max-time 2
# → 200

claude mcp list
# → code-rag が表示されれば OK
```

LAN から直接は到達できないことの確認:

```bash
nc -z -w3 <server-ip> 8765 && echo "FAIL: LAN から到達できてしまう" || echo "OK: 非到達"
```

## 5. 再接続手順

トンネルが切れると MCP 接続も切れる (Claude Code 上でツール呼出しがタイムアウト)。

1. トンネルプロセス確認: `pgrep -fl "ssh.*8765"`
2. 残骸があれば kill して張り直し: `pkill -f "ssh -f -N -L 8765"; ssh -f -N -L 8765:localhost:8765 mini`
3. `curl http://localhost:8765/sse --max-time 2` で 200 を確認
4. Claude Code 側は `/mcp` で再接続 (またはセッション再起動)

## 6. autossh による自動再接続 (任意)

```bash
brew install autossh
autossh -M 0 -f -N -L 8765:localhost:8765 mini
# -M 0: 監視ポート不使用 (ServerAliveInterval に委ねる)
```

ログイン時に自動で張りたい場合は launchd (接続元マシン側) に登録するか、シェル起動時に
`pgrep -f "autossh.*8765" || autossh -M 0 -f -N -L 8765:localhost:8765 mini` を仕込む。

## MCP ツール一覧

詳細は [README.md](README.md#mcp-ツール一覧) を参照。トンネル越しに使える主なツール:

| ツール | 用途 |
|---|---|
| `search_code` | ハイブリッド検索（ベクトル検索 + 全文検索 BM25 + リランカ）。`lang` / `path_glob` / `modified_since` (`7d`/`24h`/`YYYY-MM-DD`) / `author` (email 部分一致) フィルタ対応 |
| `search_code_debug` | `search_code` と同一ランキング + 段階別スコア内訳（vector / bm25 / rrf / rerank / diversity / consensus_guard）を返す診断ツール |
| `grep_code` | 正規表現による全文検索（`rg` 優先、無ければ Python `re` フォールバック） |
| `get_file_range` | ファイルの指定行範囲を取得（リポ外パス拒否・secret 検査・最大 300 行） |
| `find_references` | コードグラフ（`edges`）から定義/参照を種別付きで返す。グラフ未登録シンボルのみ grep 近似にフォールバック（`approximate: true`） |
| `related_code` | シンボル/ファイル起点の近傍展開（import・定義・参照の双方向 BFS、深さ 1–3、上限 50 件） |
| `list_symbols` | tree-sitter でファイル内の関数/クラス定義を列挙 |
| `list_repos` | 設定済みリポジトリ + chunk 数 + 最終インデックス時刻 |
| `stats` | 直近 N 日のクエリ集計（呼出数/レイテンシ/頻出クエリ） |

## 6.5. コードの取り込み (sync)

作業機のコードをサーバー機に同期するには `scripts/sync_repo.sh` を使う。SSH トンネル経由の rsync で差分転送し、サーバー側で差分インデックスを自動実行する:

```bash
# 作業機で実行 (トンネル or SSH 接続が必要)
scripts/sync_repo.sh my-project ~/work/my-project
```

- 初回: スナップショット作成 → インデックス → `list_repos` に出現
- 変更ゼロで再実行 → rsync 転送ゼロ → hash 一致 → 再 embed ゼロ
- ファイル削除して再実行 → 該当チャンク + edges が除去される

検索契機の自動同期 (任意) は `hooks/client/` を参照。

## サーバーの管理（サーバー機側）

サーバーは `config.yaml` の `server.host: 127.0.0.1` 以外では起動を拒否する
(意図的に外す場合のみ `CODE_RAG_ALLOW_UNSAFE_BIND=1`)。

```bash
# 起動（バックグラウンド、launchd 未使用時）
cd ~/code-rag
nohup uv run python server.py > data/logs/server.log 2>&1 & echo $! > data/server.pid

# 停止
kill $(cat ~/code-rag/data/server.pid)

# ログ確認
tail -f ~/code-rag/data/logs/server.log

# インデックス再構築 (config.yaml の repos[].name を指定)
cd ~/code-rag && uv run python indexer.py --repo my-project

# 評価実行
cd ~/code-rag && uv run python eval/run.py <label>
```

## 自動起動 (launchd)

nohup の代替。サーバー機の再起動後も自動復帰。

```bash
# 1. nohup サーバー停止
[ -f ~/code-rag/data/server.pid ] && kill $(cat ~/code-rag/data/server.pid) && rm ~/code-rag/data/server.pid

# 2. plist 配置 + 登録 (server)
cp ~/code-rag/scripts/launch_agent.plist ~/Library/LaunchAgents/com.local.code-rag.plist
launchctl load ~/Library/LaunchAgents/com.local.code-rag.plist

# 3. plist 配置 + 登録 (watcher)
cp ~/code-rag/scripts/launch_agent_watcher.plist ~/Library/LaunchAgents/com.local.code-rag-watcher.plist
launchctl load ~/Library/LaunchAgents/com.local.code-rag-watcher.plist

# 4. 起動確認 (サーバー機ローカルで)
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8765/sse --max-time 2
# → 200

# 停止
launchctl unload ~/Library/LaunchAgents/com.local.code-rag.plist
launchctl unload ~/Library/LaunchAgents/com.local.code-rag-watcher.plist
```

## Git post-commit 連携

watcher 補完用。commit 後に reindex を強制する保険。

```bash
# 対象リポジトリで実行
ln -s ~/code-rag/hooks/post-commit .git/hooks/post-commit
chmod +x .git/hooks/post-commit

# config.yaml の repos[].name と一致しないなら環境変数で指定
echo 'export CODE_RAG_REPO=project-a' >> ~/.zshrc
```

`server.py` の `/admin/reindex` エンドポイント (loopback のみ) を叩いて非同期に
`indexer.py --repo <name>` を起動する。
