# Quickstart: 検証手順 (CodeHound Intel Edition)

**前提**: Intel Mac (x86_64)、uv インストール済、対象リポ `~/code-rag-targets/fastapi` 存在。

## 1. セットアップ

```bash
cd ~/code-rag
uv sync          # Python 3.12 + 新依存 (fastembed 等)。初回は bge-small モデル DL あり
```

## 2. ユニットテスト (SC-003)

```bash
uv run pytest    # 全件 pass が条件
```

## 3. インデックス構築 (SC-002)

```bash
# Ollama を止めた状態でも完走することが受入条件
uv run python indexer.py --repo default
# 期待: data/code_rag.db 生成、chunk 数が NOTES.md 記録値 (~582) と近い
```

## 4. 検索品質ゲート (SC-001) — 最重要

```bash
uv run python eval/run.py hound_intel_port   # label は位置引数 (--label ではない)
# 期待: recall@5 >= 0.92 (目標 0.95)。結果は eval/results/ に保存
# 0.92 未満なら出荷不可。chunk 数差分 → chunking 経路、ヒット差分 → embedding/rerank を疑う
```

## 5. セキュリティ検証 (SC-004)

```bash
# loopback 強制: config の host を 0.0.0.0 にして起動 → 拒否されること
uv run python server.py --transport sse   # host=0.0.0.0 ならエラー終了
# host=127.0.0.1 に戻して起動 → 正常
# LAN 非到達確認 (MacBook から):
nc -z -w3 <mini-LAN-IP> 8765 && echo "FAIL: 到達できてしまう" || echo "OK"
```

## 6. SSH トンネル経由の E2E (SC-005)

```bash
# MacBook 側:
ssh -N -L 8765:localhost:8765 <user>@<mini>   # ~/.ssh/config にホスト登録推奨
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8765/sse --max-time 2  # 200
# Claude Code の .mcp.json: "url": "http://localhost:8765/sse"
```

## 7. 常駐運用切替 (User Story 4)

```bash
# 旧サービス停止 → 新 plist ロード
launchctl unload ~/Library/LaunchAgents/com.local.code-rag*.plist
cp scripts/launch_agent.plist ~/Library/LaunchAgents/com.local.code-rag.plist
cp scripts/launch_agent_watcher.plist ~/Library/LaunchAgents/com.local.code-rag-watcher.plist
launchctl load ~/Library/LaunchAgents/com.local.code-rag*.plist
# watcher 動作確認: 対象リポのファイルを touch → data/logs/watcher.stdout.log に差分インデックス記録
```

## 8. レイテンシ確認 (SC-006)

```bash
uv run python - <<'EOF'
import time, retrieval
t0=time.time(); retrieval.hybrid_search("authentication middleware", repo="default", k=5)
print("warm-up %.2fs" % (time.time()-t0))
t0=time.time(); retrieval.hybrid_search("dependency injection", repo="default", k=5)
print("2nd query %.2fs" % (time.time()-t0))
EOF
# 期待: 2nd query が現行 (~3s, reranker込み) 以下
```

**計測結果 (2026-06-12, T014)**: warm-up 3.34s / 2nd query 2.64s — 旧構成 avg 2974ms (phase3_final) 以下で SC-006 達成。
eval 全体: recall@5 = 0.95 (19/20, miss は既知難問 q12 のみ・ベースライン同値)、MRR 0.84、avg lat 2700ms
(`eval/results/hound_intel_port_20260612T103723Z.*`)。
