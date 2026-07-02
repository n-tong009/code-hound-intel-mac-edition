# Phase 2 - Query Preprocessing

**Timestamp**: 20260424T101449Z
**recall@5**: 0.80 (16/20)
**MRR**: 0.65
**Delta vs Phase 1 baseline**: 0 / 0

## Analysis

BM25 単独では効果あり（q03: `get_openapi` が BM25 rank 1、q18: `openapi/docs.py` が BM25 rank 1-3）。ただし `applications.py` の HTTP メソッドデコレータ chunk (get/post/trace/head/options 等) が OpenAPI 関連語を大量に含むため、RRF merge 後に沈み、最終 top5 に残らず recall は不変。

次段 (3.2 AST header) で chunk に `# symbol: get_openapi` を埋め込むことで識別子ベクトル化を試みる。

## Per-Query Results

| ID | Query | Recall | Rank |
|---|---|---|---|
| q01 | dependency injection resolution solve dependencies | ✅ | 1 |
| q02 | request body validation parsing pydantic | ✅ | 4 |
| q03 | generate OpenAPI schema operation paths | ❌ | - |
| q04 | WebSocket route registration handler session | ✅ | 2 |
| q05 | HTTP exception handler validation error response | ✅ | 1 |
| q06 | OAuth2 password bearer authentication flow token | ✅ | 1 |
| q07 | HTTP Bearer token authorization header security | ✅ | 1 |
| q08 | API key header query cookie authentication | ✅ | 1 |
| q09 | serialize response endpoint handler request | ✅ | 1 |
| q10 | background task execution async | ✅ | 1 |
| q11 | CORS middleware cross origin resource sharing | ✅ | 1 |
| q12 | path parameter query parameter extraction request | ❌ | - |
| q13 | pydantic model field compat v2 compatibility layer | ✅ | 2 |
| q14 | server sent events SSE streaming response | ✅ | 1 |
| q15 | static files serve mount directory | ✅ | 3 |
| q16 | Jinja2 template rendering HTML response | ❌ | - |
| q17 | application lifespan context manager startup shutdown | ✅ | 1 |
| q18 | Swagger UI ReDoc HTML documentation endpoint | ❌ | - |
| q19 | form data file upload multipart processing | ✅ | 2 |
| q20 | flatten dependency graph get dependant params | ✅ | 1 |
