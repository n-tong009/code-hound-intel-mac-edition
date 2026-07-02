#!/usr/bin/env bash
# hooks/client/pre-search-sync.sh — 作業機側 Claude Code の PreToolUse フック サンプル
# (specs/003 US3 / FR-007a = 検索契機の自動同期 = SHOULD)。
#
# 役割: code-rag の検索系ツールを呼ぶ直前に sync_repo.sh を同期実行 (完了待ち) し、
#       最新の作業ツリーをサーバへ反映してから検索させる。変更ゼロなら rsync が即返り、
#       差分インデックスも再 embed ゼロで待ち時間はほぼゼロ (SC-004/SC-005)。
#
# 重要: これは「クライアント側」フック。サーバはこのフックの有無に依存しない。
#       フック未導入時は手動 `sync_repo.sh` (FR-007) でフォールバックできる。
#
# 設定 (作業機の Claude Code settings.json):
#   "hooks": {
#     "PreToolUse": [
#       { "matcher": "search_code|search_code_debug|grep_code|find_references|related_code",
#         "hooks": [ { "type": "command",
#                      "command": "/path/to/code-rag/hooks/client/pre-search-sync.sh" } ] }
#     ]
#   }
#
# 環境変数 (作業機側で export。値は環境固有 / research R5):
#   CODE_RAG_REPO        対象 repo 名 (config.yaml repos[].name と一致)。必須。
#   CODE_RAG_SRC         作業ツリーのローカルパス。必須。
#   CODE_RAG_SYNC        sync_repo.sh のパス (既定: このスクリプトからの相対)。
#   CODE_RAG_REMOTE/...  sync_repo.sh が参照するリモート転送設定 (sync_repo.sh ヘッダ参照)。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC="${CODE_RAG_SYNC:-$SCRIPT_DIR/../../scripts/sync_repo.sh}"

# 必須値が無ければ何もせず通す (フックが検索をブロックしない = フェイルオープン)。
if [[ -z "${CODE_RAG_REPO:-}" || -z "${CODE_RAG_SRC:-}" ]]; then
  echo "[pre-search-sync] CODE_RAG_REPO / CODE_RAG_SRC 未設定 → 同期スキップ" >&2
  exit 0
fi

# 同期 (完了待ち)。失敗してもサーバ既存索引で検索は継続可 → 非ブロッキングで通す。
if ! "$SYNC" "$CODE_RAG_REPO" "$CODE_RAG_SRC" >&2; then
  echo "[pre-search-sync] 同期失敗 (オフライン等) → 既存索引で検索継続" >&2
fi
exit 0
