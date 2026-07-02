# Implementation Plan: CodeHound Intel Edition — code-rag の code-hound 良所取り進化

**Branch**: `001-hound-intel-port` (作業は main 直、フェーズ毎コミット) | **Date**: 2026-06-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-hound-intel-port/spec.md`

## Summary

稼働中の code-rag (lancedb 0.5.0 + Ollama embedding) を、code-hound のストレージ/セキュリティ/テスト基盤を移植して in-place 進化させる。検索品質の柱 (AST header chunking、BM25+RRF、diversity cap、256KB 上限) は code-rag 版を維持し、ストレージを SQLite + numpy BLOB、embedding を fastembed (bge-small-en-v1.5) に置換。サーバーは loopback 強制、アクセスは SSH トンネル。recall@5 ≥ 0.92 が出荷ゲート。

## Technical Context

**Language/Version**: Python 3.12 (uv 管理、Intel x86_64 CPython)

**Primary Dependencies**: fastmcp / fastembed / onnxruntime==1.17.3 / numpy<2.0 / rank-bm25 / tree-sitter==0.21.3 + tree-sitter-languages / llama-index-core (CodeSplitter 用、要確認 → research.md R3) / watchdog / pathspec / pyyaml / rich / pytest (dev)

**除去**: lancedb, llama-index-embeddings-ollama (本体 llama-index は R3 次第), httpx, pandas, flashrank, onnxruntime 重複用途 (flashrank 経由)

**Storage**: SQLite 単一ファイル `data/code_rag.db`。vector は float32 BLOB (dim=384)。cosine はオンメモリ numpy (hound 方式 `_vec_cache`)。FTS5 不使用 — BM25 は既存 rank_bm25 オンメモリを温存

**Testing**: pytest (`uv run pytest`)。検索品質は `eval/run.py` (recall@5)

**Target Platform**: Intel Mac (macOS x86_64, Darwin 24)。RAG 専用機として launchd 常駐

**Project Type**: single project (フラットモジュール構成を維持: chunking / hygiene / indexer / retrieval / server / watcher / observe / security)

**Performance Goals**: recall@5 ≥ 0.92 (目標 0.95)。embed スループット実測 128 chunks / 0.26s (Ollama 比で向上見込み)。検索レイテンシ現行同等以下

**Constraints**: onnxruntime==1.17.3 / numpy<2.0 ピン (Constitution II)。127.0.0.1 bind 強制 (Constitution III)。稼働中サービスは eval 通過まで現行のまま

**Scale/Scope**: 評価対象 fastapi リポ 1 つ (~600 chunks)。シングルユーザー

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 判定 | 根拠 |
|---|---|---|
| I. Retrieval Quality | ✅ PASS | AST header / diversity cap / 256KB / BM25+RRF を全て維持。embedding 変更は eval 再計測で検証 (quickstart.md に手順) |
| II. Intel Pins | ✅ PASS | onnxruntime==1.17.3 + numpy<2 ピン維持。fastembed 動作検証済 (research.md R1)。lancedb 廃止でピン 1 つ解消 |
| III. Local-First Security | ✅ PASS | security.py 移植、validate_server_host で loopback 強制。SSH トンネル前提、bearer token 非導入は spec の Assumptions に明記 |
| IV. Test-First | ✅ PASS | hound テスト 3 系を先に移植 → red → 実装 → green。品質は eval ゲート |
| V. Simplicity | ✅ PASS | SQLite > lancedb、依存 5 個削減。reranker (flashrank) は use_reranker=false で廃止 (research.md R2) |

**Post-Design Re-check (Phase 1 完了後)**: 違反なし。Complexity Tracking 不要。

## Project Structure

### Documentation (this feature)

```text
specs/001-hound-intel-port/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── mcp-tools.md     # MCP ツール契約 (署名・返却構造)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
code-rag/                  # フラット構成を維持 (src/ 化しない — 稼働中構成の温存)
├── chunking.py            # 維持 (AST header)。Chunk dataclass はそのまま
├── hygiene.py             # 維持 (256KB, secret scan, extra_exclude)
├── security.py            # ★新規 (hound から移植: validate_server_host, private file helpers)
├── indexer.py             # ★書換え (lancedb → SQLite DDL + fastembed embed)
├── retrieval.py           # ★書換え (vector 検索を SQLite+numpy に。Hit dataclass / hybrid_search 署名維持。hound の suggested_ranges / context_trust / infer_file_role 取込み)
├── server.py              # ★改修 (security 統合、ツール署名は現行維持、/admin/reindex 維持)
├── watcher.py             # ★改修 (SQLite upsert、fastembed)
├── observe.py             # 微修正 (private file I/O ラッパー適用のみ。ログ形式は code-rag 版維持)
├── config.yaml            # ★改修 (embedding.provider=fastembed / storage.* / server.host=127.0.0.1)
├── pyproject.toml         # ★改修 (依存差替え、Python 3.12、pytest 設定)
├── eval/                  # 維持 (run.py は Hit.to_dict() 互換で無修正目標)
├── tests/                 # ★新規 (hound から移植・適応)
│   ├── test_security.py
│   ├── test_retrieval_metadata.py
│   └── test_observe_metrics.py
├── scripts/
│   ├── launch_agent.plist         # ★改修 (新環境)
│   └── launch_agent_watcher.plist # ★改修
├── hooks/post-commit      # 維持
├── CONNECT.md             # ★書換え (SSH トンネル手順)
└── data/                  # git 管理外。code_rag.db 新規、lance.db* 残置
```

**Structure Decision**: 稼働中のフラット構成を維持。src/ 化やパッケージ化は本フィーチャーの範囲外 (Constitution V: YAGNI)。

## Complexity Tracking

違反なし — 記載不要。
