# Quickstart: コードグラフ探索の検証手順

前提: `uv sync` 済、`config.yaml` の repos に default (fastapi) 設定済。

## 1. テスト (Red → Green の確認)

```bash
uv run pytest tests/test_graph.py -q     # グラフ抽出・BFS・フォールバック
uv run pytest -q                          # 全体 (既存 54 + 新規)
```

期待: 全 green。

## 2. グラフ構築 (全量 reindex)

```bash
uv run python indexer.py --repo default
```

期待: 従来どおり ~582 chunks。続けて edges 件数確認:

```bash
uv run python -c "
import indexer
conn = indexer.get_db(indexer.load_config())
for row in conn.execute('SELECT edge_type, COUNT(*) FROM edges GROUP BY edge_type'):
    print(row[0], row[1])
"
```

期待: imports / defines / references がそれぞれ 0 より大きい。

## 3. find_references の昇格確認

```bash
uv run python -c "
from server import find_references
refs = find_references('get_openapi')
print('kinds:', {r.get('kind') for r in refs})
print('approximate:', {r.get('approximate') for r in refs})
for r in refs[:5]: print(r['path'].split('/')[-1], r['lineno'], r['kind'])
"
```

期待: `definition` と `reference` の両種別、approximate は False のみ。
コメント/文字列内のみの出現が含まれないこと (SC-002) は test_graph.py が担保。

グラフ未登録シンボル (例: 適当な英単語) で `approximate: true` のフォールバックが返ることも確認。

## 4. related_code の確認

```bash
uv run python -c "
from server import related_code
out = related_code('fastapi/routing.py', depth=1)
print('error:', out['error'], 'count:', len(out['related']), 'truncated:', out['truncated'])
for r in out['related'][:8]: print(r['edge_type'], r['kind'], str(r['value']).split('/')[-1])
"
```

期待: imports / imported_by / defines が混在、error は None。
depth=100 を渡しても 3 に丸められクラッシュしない (FR-004)。

## 5. eval ゲート (SC-001)

```bash
uv run python eval/run.py code_graph_search
```

期待: recall@5 = 0.95 (19/20、miss は q12 のみ)。0.92 未満なら差し戻し。

## 6. watcher 同期 (SC-005)

watcher 稼働中に対象リポのファイルへ import 文を追加 → 保存 → 数秒後:

```bash
uv run python -c "
import indexer
conn = indexer.get_db(indexer.load_config())
print(conn.execute(\"SELECT COUNT(*) FROM edges WHERE path LIKE '%<編集したファイル>%'\").fetchone()[0])
"
```

期待: 追加 import が edge に現れる。行を戻すと消える。

## 7. サービス再起動 (eval 通過後のみ)

```bash
launchctl unload ~/Library/LaunchAgents/com.local.code-rag.plist
launchctl unload ~/Library/LaunchAgents/com.local.code-rag-watcher.plist
launchctl load ~/Library/LaunchAgents/com.local.code-rag.plist
launchctl load ~/Library/LaunchAgents/com.local.code-rag-watcher.plist
lsof -nP -iTCP:8765 -sTCP:LISTEN   # 127.0.0.1:8765 を確認
```

## 8. 旧 DB フォールバック (FR-009)

edges 未構築 DB でも find_references が grep フォールバックで動くこと:
test_graph.py のフォールバックテストが担保 (手動確認は edges を一時 DELETE して find_references → approximate: true)。
