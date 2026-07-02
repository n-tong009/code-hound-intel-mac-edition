#!/usr/bin/env bash
# scripts/watch_and_sync.sh — Mac B 常駐: fswatch でソース監視 → sync_repo.sh 実行
#
# 使い方:
#   watch_and_sync.sh <repo_name> <src_path>
#   例: watch_and_sync.sh static-site-generator ~/project/static-site-generator
#
# 環境変数 (sync_repo.sh に透過):
#   CODE_RAG_REMOTE   リモートホスト (例: mini)
#   CODE_RAG_ROOT     サーバ側 code-rag ルート
#   CODE_RAG_SSH_PORT SSH ポート (既定 22)
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: watch_and_sync.sh <repo_name> <src_path>" >&2
  exit 2
fi

REPO_NAME="$1"
SRC_PATH="$2"

if [[ ! -d "$SRC_PATH" ]]; then
  echo "[watch] source not found: $SRC_PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="${SCRIPT_DIR}/sync_repo.sh"

if [[ ! -x "$SYNC_SCRIPT" ]]; then
  echo "[watch] sync_repo.sh not found or not executable: $SYNC_SCRIPT" >&2
  exit 1
fi

if ! command -v fswatch &>/dev/null; then
  echo "[watch] fswatch not found. Install: brew install fswatch" >&2
  exit 1
fi

DEBOUNCE=2  # 秒

echo "[watch] monitoring: ${SRC_PATH}"
echo "[watch] repo: ${REPO_NAME}"
echo "[watch] debounce: ${DEBOUNCE}s"
echo "[watch] sync script: ${SYNC_SCRIPT}"
echo "[watch] Ctrl+C to stop"

fswatch -o -l "$DEBOUNCE" \
  --exclude '\.git/' \
  --exclude '__pycache__/' \
  --exclude 'node_modules/' \
  --exclude '\.DS_Store' \
  "$SRC_PATH" | while read -r _count; do
  echo "[watch] $(date '+%H:%M:%S') change detected, syncing..."
  if "$SYNC_SCRIPT" "$REPO_NAME" "$SRC_PATH"; then
    echo "[watch] $(date '+%H:%M:%S') sync done"
  else
    echo "[watch] $(date '+%H:%M:%S') sync FAILED (exit $?)" >&2
  fi
done
