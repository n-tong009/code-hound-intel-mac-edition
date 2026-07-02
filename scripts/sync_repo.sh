#!/usr/bin/env bash
# scripts/sync_repo.sh — 取り込み同期 CLI (specs/003 US2 / FR-005・FR-006・FR-009・FR-013・FR-014)
#
# 作業機 (Mac B) のプロジェクトを RAG サーバ (Mac A) のスナップショットへ差分転送し、
# サーバ側で差分インデックスをトリガする。SSH トンネル経由・片方向 (作業機 → サーバ)。
#
# 使い方:
#   scripts/sync_repo.sh <repo_name> <src_path>
#   例: scripts/sync_repo.sh market_brief ~/work/Market_Brief
#
# 環境変数 (research R5 の環境固有値の外出し):
#   CODE_RAG_REMOTE   非空ならリモート転送 (例: user@127.0.0.1)。未設定ならローカル同期 (同一機/テスト)。
#   CODE_RAG_SSH_PORT SSH トンネルのローカル転送ポート (既定 22)。127.0.0.1 ローカルフォワード前提 (FR-013)。
#   CODE_RAG_ROOT     サーバ側 code-rag ルート (リモート時のインデックス実行 cwd。既定: このスクリプトの親の親)。
#   CODE_RAG_MAX_SIZE rsync --max-size (既定 262144 = hygiene max_file_bytes と一致)。
#
# 設計上の注意:
#   - rsync 段の除外は **パス/パターン + サイズのみ** (.git/node_modules/生成物/.gitignore/.env 等)。
#     秘密情報の **内容ベース検知は索引段 (hygiene.py) の責務** — rsync は内容を走査しない (FR-009)。
#   - スナップショット保管先 data/snapshots/<repo>/ は **0700** で作成・維持 (平文複製の閲覧限定 / FR-014)。
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: sync_repo.sh <repo_name> <src_path>" >&2
  exit 2
fi

REPO_NAME="$1"
SRC_PATH="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CODE_RAG_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
MAX_SIZE="${CODE_RAG_MAX_SIZE:-262144}"
SSH_PORT="${CODE_RAG_SSH_PORT:-22}"

REL_DEST="data/snapshots/${REPO_NAME}"

# --- Auto-register: config.yaml に repo が無ければ追加 (yaml 不要) ---
_register_repo() {
  local cfg="$1" repo="$2"
  if grep -q "name: ${repo}$" "$cfg" 2>/dev/null; then
    return 0
  fi
  if grep -q '^embedding:' "$cfg"; then
    sed -i.bak "/^embedding:/i\\
\\  - name: ${repo}\\
\\    path: data/snapshots/${repo}\\
\\    languages: [python, typescript, javascript]\\
\\
" "$cfg" && rm -f "${cfg}.bak"
  else
    printf '\n  - name: %s\n    path: data/snapshots/%s\n    languages: [python, typescript, javascript]\n' "$repo" "$repo" >> "$cfg"
  fi
  echo "[sync_repo] auto-registered repo: ${repo}"
}

if [[ -n "${CODE_RAG_REMOTE:-}" ]]; then
  ssh -p "$SSH_PORT" "$CODE_RAG_REMOTE" bash -s -- "'${ROOT}/config.yaml'" "'${REPO_NAME}'" << 'REGEOF'
cfg="$1"; repo="$2"
if grep -q "name: ${repo}$" "$cfg" 2>/dev/null; then exit 0; fi
if grep -q '^embedding:' "$cfg"; then
  sed -i.bak "/^embedding:/i\\
\\  - name: ${repo}\\
\\    path: data/snapshots/${repo}\\
\\    languages: [python, typescript, javascript]\\
" "$cfg" && rm -f "${cfg}.bak"
else
  printf '\n  - name: %s\n    path: data/snapshots/%s\n    languages: [python, typescript, javascript]\n' "$repo" "$repo" >> "$cfg"
fi
echo "[sync_repo] auto-registered repo: ${repo}"
REGEOF
else
  _register_repo "${ROOT}/config.yaml" "${REPO_NAME}"
fi

if [[ ! -d "$SRC_PATH" ]]; then
  echo "[sync_repo] source not found: $SRC_PATH" >&2
  exit 1
fi

# rsync 除外 (パターンのみ)。.gitignore も尊重。.env 等は名前で除外。
RSYNC_FILTERS=(
  --filter=':- .gitignore'
  --exclude='.git/'
  --exclude='node_modules/'
  --exclude='.venv/'
  --exclude='venv/'
  --exclude='__pycache__/'
  --exclude='dist/'
  --exclude='build/'
  --exclude='*.pyc'
  --exclude='.DS_Store'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='*.log'
)

RSYNC_OPTS=(-az --delete "--max-size=${MAX_SIZE}" "${RSYNC_FILTERS[@]}")

if [[ -n "${CODE_RAG_REMOTE:-}" ]]; then
  # リモート: 作業機 → サーバ。SSH トンネル (127.0.0.1 ローカルフォワード) 経由。
  DEST="${CODE_RAG_REMOTE}:${ROOT}/${REL_DEST}/"
  echo "[sync_repo] rsync (remote) ${SRC_PATH}/ -> ${DEST}"
  ssh -p "$SSH_PORT" "$CODE_RAG_REMOTE" \
    "mkdir -p '${ROOT}/${REL_DEST}' && chmod 700 '${ROOT}/${REL_DEST}'"
  rsync "${RSYNC_OPTS[@]}" -e "ssh -p ${SSH_PORT}" "${SRC_PATH%/}/" "$DEST"
  ssh -p "$SSH_PORT" "$CODE_RAG_REMOTE" "chmod 700 '${ROOT}/${REL_DEST}'"
  echo "[sync_repo] trigger differential index on server for '${REPO_NAME}'"
  ssh -p "$SSH_PORT" "$CODE_RAG_REMOTE" \
    "export PATH=\"/opt/homebrew/bin:/usr/local/bin:\$PATH\"; cd '${ROOT}' && uv run python indexer.py --repo '${REPO_NAME}'"
else
  # ローカル: 同一機 / テスト。
  DEST_DIR="${ROOT}/${REL_DEST}"
  mkdir -p "$DEST_DIR"
  chmod 700 "$DEST_DIR"
  echo "[sync_repo] rsync (local) ${SRC_PATH}/ -> ${DEST_DIR}/"
  rsync "${RSYNC_OPTS[@]}" "${SRC_PATH%/}/" "${DEST_DIR}/"
  chmod 700 "$DEST_DIR"
  echo "[sync_repo] trigger differential index for '${REPO_NAME}'"
  ( cd "$ROOT" && uv run python indexer.py --repo "$REPO_NAME" )
fi

echo "[sync_repo] done: ${REPO_NAME}"
