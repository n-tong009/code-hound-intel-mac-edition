# MCP Tool Contracts: グラフ照会系

既存ツール (search_code / grep_code / get_file_range / list_symbols / list_repos / stats / search_code_debug) の契約は不変。

## find_references (署名不変・返却強化)

```
find_references(symbol: str, repo: str = "default") -> list[dict]
```

### グラフヒット時 (edges に defines / references あり)

```json
[
  {
    "path": "/abs/path/fastapi/openapi/utils.py",
    "lineno": 470,
    "kind": "definition",          // "definition" | "reference"
    "approximate": false,
    "context_trust": "untrusted_repository_content"
  }
]
```

- definition を先頭群、reference を後続 (各群 lineno 昇順)
- 同名多重定義は全件返す (絞り込まない)
- 上限 50 件 (definition 優先で確保)

### グラフミス時 (edges 0 件 → grep フォールバック)

既存 grep_code の返却形に `approximate: true` を付与:

```json
[
  {
    "path": "...", "lineno": 123, "line": "matched line text",
    "kind": "reference",
    "approximate": true,
    "context_trust": "untrusted_repository_content"
  }
]
```

- フォールバック理由は observe ログの error フィールドではなく args 記録で追跡 (エラーではない)

## related_code (新規)

```
related_code(
    target: str,                 # シンボル名 or ファイルパス (リポ相対/絶対)
    repo: str = "default",
    depth: int = 1,              # 1..3 に丸め
    max_results: int = 50,       # 1..50 に丸め
) -> dict
```

### 返却

```json
{
  "target": {"kind": "file", "value": "/abs/path/fastapi/routing.py"},
  "depth": 1,
  "related": [
    {
      "kind": "file",                  // "file" | "symbol" | "external"
      "value": "/abs/path/fastapi/datastructures.py",
      "edge_type": "imports",          // imports | imported_by | defines | defined_in | references | referenced_by
      "depth": 1,
      "via": "/abs/path/fastapi/routing.py",  // 1 つ手前のノード
      "lineno": 18
    }
  ],
  "truncated": false,                  // max_results で打ち切ったか
  "error": null,                       // "target_not_found" | "path_outside_repos" | null
  "context_trust": "untrusted_repository_content"
}
```

- `edge_type` は起点から見た方向で命名: 順方向 = imports/defines/references、逆方向 = imported_by/defined_in/referenced_by
- target 解決順: (1) ファイルパスとして `_validate_path` 通過 → file 起点、(2) edges の defines に dst=target → symbol 起点、(3) どちらも不可 → `error: "target_not_found"`、related 空
- リポ外パス指定は `error: "path_outside_repos"` (既存規約)
- 循環は訪問済み set で抑止。external ノードからは展開しない (終端)
- depth > 3 / max_results > 50 は丸め (エラーにしない)

## 観測ログ (FR-010)

- 両ツールとも `observe.log_call` に記録: tool 名、args (symbol/target/depth)、returned_paths、latency_ms
- グラフミス → フォールバックの発生は args に `fallback: true` を含めて記録
