# Phase 2 - Embedding A/B (Inconclusive)

| model | params | recall@5 | MRR | avg latency (ms) | 判定 |
|---|---|---|---|---|---|
| nomic-embed-text | 137M | 0.95 (19/20) | 0.79 | 2808 | ✅ baseline retained |
| bge-m3 | 567M | — | — | — | ⛔ indexing infeasible |

## bge-m3 が完走しなかった理由

Intel Mac mini (x86_64) では Ollama の埋め込み計算が CPU 推論のみで、bge-m3（567M パラメータ）は nomic-embed-text（137M）の **約 4 倍**の計算量。実測：

- nomic-embed-text: バッチ 128 で 1 batch ≒ 6 分、582 chunk 全件で **約 1 時間**
- bge-m3: バッチ 128 で 1 batch も 1 時間以上経っても完了せず（Ollama runner は 545% CPU で busy）

合計推定インデックス時間 4-5h+ と見積もり、Phase 2 の時間枠では完走不可と判断して中止。`data/lance.db.bge-m3` の partial DB は削除済み。

## 判定

`nomic-embed-text` を継続使用。3.1 (query prep) + 3.2 (AST header) + 後段の **diversity cap** で recall@5 = 0.95 (+18.75% 相対) を達成しているため、Exit 条件 (≥ 0.92) は embedding 交換なしで満たしている。

## Phase 3 以降への申し送り

- bge-m3 を試すなら Linux/CUDA 環境への移行と合わせるべき。
- もしくは `bge-small-en-v1.5` 等、より軽量なコード対応モデルを次回試す。
- `eval/compare_embeddings.py` は完成済み。Linux 移行後に `--sample` 付きで実行すれば A/B が走る。
