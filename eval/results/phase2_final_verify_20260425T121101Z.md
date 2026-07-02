# Phase 2 Final Verification

**Timestamp**: 20260425T121101Z
**recall@5**: 0.95 (19/20)
**MRR**: 0.79
**avg latency**: 2811 ms

## Per-Query Results

| ID | Query | Recall | Rank | Latency (ms) |
|---|---|---|---|---|
| q01 | dependency injection resolution solve dependencies | ✅ | 1 | 4771 |
| q02 | request body validation parsing pydantic | ✅ | 2 | 2821 |
| q03 | generate OpenAPI schema operation paths | ✅ | 5 | 2412 |
| q04 | WebSocket route registration handler session | ✅ | 1 | 2798 |
| q05 | HTTP exception handler validation error response | ✅ | 1 | 2672 |
| q06 | OAuth2 password bearer authentication flow token | ✅ | 1 | 2826 |
| q07 | HTTP Bearer token authorization header security | ✅ | 1 | 2299 |
| q08 | API key header query cookie authentication | ✅ | 1 | 2304 |
| q09 | serialize response endpoint handler request | ✅ | 1 | 2565 |
| q10 | background task execution async | ✅ | 1 | 2688 |
| q11 | CORS middleware cross origin resource sharing | ✅ | 1 | 2871 |
| q12 | path parameter query parameter extraction request | ❌ | - | 2861 |
| q13 | pydantic model field compat v2 compatibility layer | ✅ | 1 | 2993 |
| q14 | server sent events SSE streaming response | ✅ | 1 | 2895 |
| q15 | static files serve mount directory | ✅ | 3 | 2530 |
| q16 | Jinja2 template rendering HTML response | ✅ | 1 | 2869 |
| q17 | application lifespan context manager startup shutdown | ✅ | 1 | 2852 |
| q18 | Swagger UI ReDoc HTML documentation endpoint | ✅ | 4 | 2370 |
| q19 | form data file upload multipart processing | ✅ | 2 | 2881 |
| q20 | flatten dependency graph get dependant params | ✅ | 1 | 2950 |
