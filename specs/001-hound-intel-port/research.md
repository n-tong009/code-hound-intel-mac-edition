# Research: CodeHound Intel Edition

**Date**: 2026-06-12 | **Plan**: [plan.md](./plan.md)

## R1: fastembed は Intel Mac (x86_64) で動くか

- **Decision**: 採用。`fastembed>=0.4` + `onnxruntime==1.17.3` + `numpy<2`
- **Rationale**: 本機で実測検証済 (2026-06-12)。bge-small-en-v1.5: model load 6.1s、128 chunks embed 0.26s。Ollama HTTP 経由より高速、外部プロセス依存も消える
- **Alternatives considered**: Ollama nomic-embed-text 継続 (実績ありだが外部プロセス依存・遅い)、bge-m3 (Phase 2 で CPU 断念済)
- **罠**: numpy を 2 系にすると onnxruntime import が `_ARRAY_API not found` で即死。ピン必須

## R2: flashrank reranker を除去できるか

- **Decision**: **維持** (use_reranker: true のまま)。当初計画の「false 化 + 依存除去」は撤回
- **Rationale**: recall@5 0.95 ベースラインは reranker ON で計測されている (eval/results/phase2_final.md, phase3_final.md で `use_reranker: true` 確認)。diversity cap は reranker 後段に実装されており、除去は実証済み施策の解体 = Constitution I 違反。flashrank は onnxruntime 1.17.3 で現に稼働中、Intel 互換問題なし
- **Alternatives considered**: 除去して eval で確認 → 移行変数が増え、recall 劣化時の原因切り分けが困難になるため却下。移行完了後に A/B 計測して除去判断するのは将来課題として可
- **影響**: pyproject から flashrank を消さない

## R3: llama-index 依存をどうするか

- **Decision**: `llama-index` (メタパッケージ) → `llama-index-core` に縮小。`llama-index-embeddings-ollama` は除去
- **Rationale**: chunking.py の import は `from llama_index.core.node_parser import CodeSplitter` のみ (chunking.py:8)。core だけで足りる。chunking 経路を変えると chunk 数が変動し eval が壊れる (NOTES.md: CodeSplitter 経路 vs fallback 経路で chunk 数が大きく変わる) ため、chunking.py 本体は無修正
- **Alternatives considered**: CodeSplitter 自前再実装 (chunk 挙動変動リスク、YAGNI)、llama-index フル維持 (不要依存)

## R4: BM25 を FTS5 にするか rank_bm25 のままか

- **Decision**: rank_bm25 オンメモリを維持。hound の FTS5 は採用しない
- **Rationale**: BM25 スコアリングとトークナイズの挙動が recall 0.95 の構成要素。FTS5 はトークナイザが異なり再現性が崩れる。`invalidate_bm25_cache(repo)` API も watcher が依存。オンメモリの起動コストは ~600 chunks では無視できる (NOTES.md の大規模懸念は Phase 外)
- **Alternatives considered**: FTS5 (大規模で有利だが品質再現性リスク)

## R5: SQLite スキーマ設計

- **Decision**: hound DDL をベースに Phase 3 カラムを追加した単一 `chunks` テーブル + `repos` テーブル。timestamp は ISO 8601 文字列、vector は float32 BLOB
- **Rationale**: hound DDL には commit_sha / last_modified / last_author が無い (調査済)。code-rag の `modified_since` / `author` フィルタと stats がこれらに依存するため必須。詳細は [data-model.md](./data-model.md)
- **Alternatives considered**: FTS5 virtual table 併設 (R4 で不採用)

## R6: 既存データの移行

- **Decision**: データ移行なし。全リポジトリを新ストレージへ再インデックス
- **Rationale**: embedding モデルが nomic-embed-text (dim=768) → bge-small-en-v1.5 (dim=384) に変わるため vector は再生成必須。chunk テキストも再生成が安全。`data/lance.db*` は退避資産として残置
- **Alternatives considered**: lancedb → SQLite のレコード変換 (vector が無価値なので意味なし)

## R7: Python 3.12 は Intel Mac で使えるか

- **Decision**: Python 3.12 (uv 取得の CPython x86_64)
- **Rationale**: R1 の検証を `uv run --python 3.12` で実施済み = 本機で 3.12 動作確認済み
- **Alternatives considered**: 3.11 維持 (hound との差分が増えるだけ)

## R8: サーバー公開方式

- **Decision**: `server.host: 127.0.0.1` + security.py の `validate_server_host` で強制。MacBook からは `ssh -L 8765:localhost:8765 <mini>`。bearer token 非導入
- **Rationale**: ユーザー決定 (2026-06-12)。SSH が認証・暗号化を兼ねる。DHCP IP 変動問題も SSH ホスト管理 (~/.ssh/config) 側に寄せられる
- **Alternatives considered**: 0.0.0.0 + bearer token (露出面が広い)、Tailscale (導入コスト)
