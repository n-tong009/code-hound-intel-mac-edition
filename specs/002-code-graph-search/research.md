# Research: コードグラフ探索

技術コンテキストに NEEDS CLARIFICATION なし。設計判断の根拠を記録する。

## R1. 参照抽出の方式

- **Decision**: tree-sitter AST の `call` ノード (関数呼出し) と `attribute` / `identifier` ノードの名前収集による名前ベース静的解析。定義は既存 `chunking._extract_symbols` を再利用
- **Rationale**: コメント (`comment` ノード) と文字列 (`string` ノード) は AST 上で別種別になるため、ノード walk から自然に除外される — grep 近似の誤検出 (SC-002) を構造的に解消。完全な型解決 (jedi / pyright 相当) は依存追加と複雑性が Principle V に反する
- **Alternatives considered**:
  - jedi / LSP ベースの解決: 精度最高だが重依存 + Intel 検証コスト。YAGNI
  - grep 改良 (コメント行除外正規表現): 文字列リテラル・行内コメントの正確な除外は正規表現では不可能。AST が正攻法

## R2. import のファイル解決

- **Decision**: `import a.b` / `from a.b import c` のモジュールパス `a.b` を、リポジトリルートからの相対で `a/b.py` → `a/b/__init__.py` の順に解決。相対 import (`from . import x`) は当該ファイルのディレクトリ起点で解決。解決できないもの (標準ライブラリ・外部パッケージ) は `dst_kind=external` として終端記録
- **Rationale**: fastapi リポはトップレベルパッケージ `fastapi/` の素直な構成で、この規則で大半が解決する。external も edge として残すことで「このファイルは何に依存しているか」を一覧可能
- **Alternatives considered**: sys.path シミュレーション (過剰)、external を捨てる (情報損失)

## R3. references edge の終点表現

- **Decision**: `dst_kind=symbol`、`dst=シンボル名` (名前文字列)。定義への解決は照会時に `defines` edge と JOIN して行う
- **Rationale**: 抽出時に解決を固定すると、同名多重定義の選択を誤ったまま永続化する。名前で持てば照会時に全候補を返せる (spec Assumptions 準拠)。watcher の差分更新も「そのファイルの references を消して引き直す」だけで済む
- **Alternatives considered**: 抽出時にチャンク id へ解決 (定義側の変更で全参照 edge が陳腐化し、差分同期が壊れる)

## R4. ノイズ抑制 (references の選別)

- **Decision**: references は (a) `call` ノードの関数名、(b) クラス継承 (`class X(Base)` の Base)、に限定。裸の `identifier` 全収集はしない
- **Rationale**: 全 identifier を拾うと変数名・引数名で edge が爆発し (1 ファイル数百件)、S/N が下がる。「呼んでいる・継承している」が find_references / related_code の答えとして最も価値が高い
- **Alternatives considered**: 全 identifier 収集 + 出現回数集約 (規模が許せば将来拡張可。v1 は絞る)

## R5. BFS の実装位置

- **Decision**: Python 側で実装 (SQLite に対し 1 ホップずつ `SELECT ... WHERE src IN (...)` を depth 回)。再帰 CTE は使わない
- **Rationale**: 深さ max 3 / 件数上限 50 では往復 3 回で十分高速 (SC-004 余裕)。Python 実装は訪問済み管理・上限丸め・方向別の整形が素直
- **Alternatives considered**: `WITH RECURSIVE` (デバッグしづらく、上限/方向/種別のフィルタが複雑化)

## R6. 旧 DB のマイグレーション

- **Decision**: `get_db` は `CREATE TABLE IF NOT EXISTS edges` のみ。FTS5 のような自動バックフィルはしない。edges 空のときツールはフォールバック (find_references → grep) または空結果 + 案内を返し、完全なグラフは全量 reindex で構築
- **Rationale**: edges の構築には全ファイルの AST 再パースが必要で、get_db (接続時) にやると watcher / server 起動が分単位でブロックされる。FTS5 バックフィル (chunks テーブルの転記のみ) とは計算量が違う
- **Alternatives considered**: 接続時自動バックフィル (起動ブロック、却下)、バックグラウンドスレッド構築 (複雑性、YAGNI — reindex 1 回で済む)

## R7. eval ゲートの扱い

- **Decision**: retrieval.py / chunking.py に非接触のため recall@5 は理論上不変。それでも constitution Principle I に従い `uv run python eval/run.py code_graph_search` で計測し、0.95 / miss=q12 のみであることを確認してからサービス再起動
- **Rationale**: 「触っていないから測らない」は過去の BM25 tie バグ (握りつぶしで発覚遅延) の教訓に反する。計測は 10 分で済む
