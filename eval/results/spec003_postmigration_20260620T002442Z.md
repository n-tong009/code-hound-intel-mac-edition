# specs/003 post-migration recall check

**Timestamp**: 20260620T002442Z
**recall@5**: 0.95 (19/20)
**MRR**: 0.82
**avg latency**: 55 ms

## Per-Query Results

| ID | Query | Recall | Rank | Latency (ms) |
|---|---|---|---|---|
| q01 | dependency injection resolution solve dependencies | ✅ | 1 | 709 |
| q02 | request body validation parsing pydantic | ✅ | 1 | 25 |
| q03 | generate OpenAPI schema operation paths | ✅ | 1 | 21 |
| q04 | WebSocket route registration handler session | ✅ | 1 | 39 |
| q05 | HTTP exception handler validation error response | ✅ | 1 | 19 |
| q06 | OAuth2 password bearer authentication flow token | ✅ | 1 | 19 |
| q07 | HTTP Bearer token authorization header security | ✅ | 1 | 18 |
| q08 | API key header query cookie authentication | ✅ | 1 | 19 |
| q09 | serialize response endpoint handler request | ✅ | 1 | 19 |
| q10 | background task execution async | ✅ | 1 | 18 |
| q11 | CORS middleware cross origin resource sharing | ✅ | 1 | 19 |
| q12 | path parameter query parameter extraction request | ✅ | 5 | 19 |
| q13 | pydantic model field compat v2 compatibility layer | ✅ | 2 | 21 |
| q14 | server sent events SSE streaming response | ✅ | 1 | 19 |
| q15 | static files serve mount directory | ✅ | 4 | 19 |
| q16 | Jinja2 template rendering HTML response | ❌ | - | 18 |
| q17 | application lifespan context manager startup shutdown | ✅ | 1 | 18 |
| q18 | Swagger UI ReDoc HTML documentation endpoint | ✅ | 1 | 19 |
| q19 | form data file upload multipart processing | ✅ | 1 | 19 |
| q20 | flatten dependency graph get dependant params | ✅ | 2 | 20 |
