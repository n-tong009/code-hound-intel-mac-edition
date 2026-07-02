# Data Model: コードグラフ探索

## edges テーブル (新規、indexer.DDL に追加)

```sql
CREATE TABLE IF NOT EXISTS edges (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    repo      TEXT NOT NULL,        -- repos.name と対応
    src_kind  TEXT NOT NULL,        -- 'file' | 'symbol'
    src       TEXT NOT NULL,        -- ファイル絶対パス or シンボル名
    dst_kind  TEXT NOT NULL,        -- 'file' | 'symbol' | 'external'
    dst       TEXT NOT NULL,        -- ファイル絶対パス / シンボル名 / 外部モジュール名
    edge_type TEXT NOT NULL,        -- 'imports' | 'defines' | 'references'
    path      TEXT NOT NULL,        -- この edge が抽出されたファイル (差分同期の単位)
    lineno    INTEGER               -- 出現行 (1-indexed)。defines は定義開始行
);
CREATE INDEX IF NOT EXISTS idx_edges_repo_src ON edges(repo, src);
CREATE INDEX IF NOT EXISTS idx_edges_repo_dst ON edges(repo, dst);
CREATE INDEX IF NOT EXISTS idx_edges_repo_path ON edges(repo, path);
```

### edge_type 別のフィールド規約

| edge_type | src_kind / src | dst_kind / dst | lineno |
|---|---|---|---|
| imports | file / import 元ファイル | file / 解決済みリポ内ファイル、または external / モジュール名 | import 文の行 |
| defines | file / 定義ファイル | symbol / シンボル名 | 定義開始行 |
| references | file / 参照元ファイル | symbol / 参照先シンボル名 | 呼出し・継承の行 |

- `path` は常に「この edge を生んだファイル」= src と一致 (差分同期は `DELETE WHERE repo=? AND path=?` の 1 文で済む)
- references の dst は名前文字列。定義への解決は照会時に `defines` と JOIN (research.md R3)
- ファイルパスは chunks.path と同じ絶対パス表記で統一 (JOIN 可能性の確保)

## 整合性ルール

- **全量再構築 (indexer)**: `DELETE FROM edges WHERE repo=?` → 全ファイル抽出 INSERT。chunks と同一トランザクション粒度 (FR-007)
- **差分同期 (watcher)**: ファイル変更/削除時 `DELETE FROM edges WHERE repo=? AND path=?` → (存在すれば) 再抽出 INSERT。chunks 更新と同じ `_apply_lock` 内 (FR-006)
- **孤立 edge**: path 単位の DELETE が src 側を一掃する。dst 側がダングリング (削除済みファイルを指す imports) になり得るが、照会時に存在しない dst は結果から落とすだけで実害なし — 次の全量 reindex で消える

## 照会パターン

```sql
-- find_references: 定義
SELECT path AS file, lineno, 'definition' AS kind FROM edges
WHERE repo=? AND edge_type='defines' AND dst=?;

-- find_references: 参照
SELECT path AS file, lineno, 'reference' AS kind FROM edges
WHERE repo=? AND edge_type='references' AND dst=?;

-- BFS 1 ホップ (起点 = ファイル群): import 双方向 + 定義シンボル
SELECT * FROM edges WHERE repo=? AND src IN (...);          -- 順方向
SELECT * FROM edges WHERE repo=? AND dst IN (...);          -- 逆方向
```

## 既存テーブルとの関係

- `chunks`: 無変更。related_code がシンボル → チャンク本体を返す際は `chunks (repo, path)` + 行範囲の重なりで対応チャンクを引く (任意、v1 は位置情報のみでも可 — contracts 参照)
- `repos` / `chunks_fts`: 無変更
