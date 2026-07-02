# hound_intel_port_postreview

**Timestamp**: 20260612T125246Z
**recall@5**: 0.95 (19/20)
**MRR**: 0.84
**avg latency**: 2430 ms

## Per-Query Results

| ID | Query | Recall | Rank | Latency (ms) |
|---|---|---|---|---|
| q01 | dependency injection resolution solve dependencies | ✅ | 1 | 3497 |
| q02 | request body validation parsing pydantic | ✅ | 3 | 2351 |
| q03 | generate OpenAPI schema operation paths | ✅ | 5 | 2557 |
| q04 | WebSocket route registration handler session | ✅ | 1 | 2279 |
| q05 | HTTP exception handler validation error response | ✅ | 1 | 2270 |
| q06 | OAuth2 password bearer authentication flow token | ✅ | 1 | 2361 |
| q07 | HTTP Bearer token authorization header security | ✅ | 1 | 1886 |
| q08 | API key header query cookie authentication | ✅ | 1 | 2208 |
| q09 | serialize response endpoint handler request | ✅ | 1 | 2462 |
| q10 | background task execution async | ✅ | 1 | 2463 |
| q11 | CORS middleware cross origin resource sharing | ✅ | 1 | 2352 |
| q12 | path parameter query parameter extraction request | ❌ | - | 2455 |
| q13 | pydantic model field compat v2 compatibility layer | ✅ | 1 | 2462 |
| q14 | server sent events SSE streaming response | ✅ | 1 | 2451 |
| q15 | static files serve mount directory | ✅ | 5 | 2453 |
| q16 | Jinja2 template rendering HTML response | ✅ | 1 | 2468 |
| q17 | application lifespan context manager startup shutdown | ✅ | 1 | 2333 |
| q18 | Swagger UI ReDoc HTML documentation endpoint | ✅ | 1 | 2490 |
| q19 | form data file upload multipart processing | ✅ | 1 | 2345 |
| q20 | flatten dependency graph get dependant params | ✅ | 1 | 2464 |
