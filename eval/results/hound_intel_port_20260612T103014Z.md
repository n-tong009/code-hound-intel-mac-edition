# hound_intel_port

**Timestamp**: 20260612T103014Z
**recall@5**: 0.90 (18/20)
**MRR**: 0.83
**avg latency**: 2700 ms

## Per-Query Results

| ID | Query | Recall | Rank | Latency (ms) |
|---|---|---|---|---|
| q01 | dependency injection resolution solve dependencies | ✅ | 1 | 3483 |
| q02 | request body validation parsing pydantic | ✅ | 3 | 2641 |
| q03 | generate OpenAPI schema operation paths | ❌ | - | 2761 |
| q04 | WebSocket route registration handler session | ✅ | 1 | 2565 |
| q05 | HTTP exception handler validation error response | ✅ | 1 | 2562 |
| q06 | OAuth2 password bearer authentication flow token | ✅ | 1 | 2620 |
| q07 | HTTP Bearer token authorization header security | ✅ | 1 | 2171 |
| q08 | API key header query cookie authentication | ✅ | 1 | 2491 |
| q09 | serialize response endpoint handler request | ✅ | 1 | 2765 |
| q10 | background task execution async | ✅ | 1 | 2759 |
| q11 | CORS middleware cross origin resource sharing | ✅ | 1 | 2668 |
| q12 | path parameter query parameter extraction request | ❌ | - | 2759 |
| q13 | pydantic model field compat v2 compatibility layer | ✅ | 1 | 2738 |
| q14 | server sent events SSE streaming response | ✅ | 1 | 2744 |
| q15 | static files serve mount directory | ✅ | 5 | 2753 |
| q16 | Jinja2 template rendering HTML response | ✅ | 1 | 2759 |
| q17 | application lifespan context manager startup shutdown | ✅ | 1 | 2631 |
| q18 | Swagger UI ReDoc HTML documentation endpoint | ✅ | 1 | 2753 |
| q19 | form data file upload multipart processing | ✅ | 1 | 2636 |
| q20 | flatten dependency graph get dependant params | ✅ | 1 | 2737 |
