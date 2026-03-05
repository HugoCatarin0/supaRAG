# supaRAG

Rust REST microservice (separate repo) that exposes a frontend-friendly API surface compatible with the LightRAG WebUI client contract by proxying upstream LightRAG endpoints.

## Why this project

This service lets you keep your own frontend(s) while preserving existing LightRAG functionality:

- document APIs (upload/scan/status/reprocess/delete/cache)
- graph APIs (labels, graph query, entity/relation edit)
- query APIs (`/query`, `/query/stream`)
- auth passthrough (`/login`, `/auth-status`) when used upstream

It is intentionally designed as a thin compatibility microservice so your frontend can call one stable Rust API.

## Current architecture

- Rust HTTP server: `axum`
- Upstream calls: `reqwest`
- Stream passthrough for `POST /query/stream`
- CORS enabled for custom frontends
- Optional default auth header injection via environment variables

All incoming routes (except root/meta) are forwarded to upstream LightRAG:

- `GET /` → service info
- `GET /_meta/health` → local proxy health + configured upstream URL
- `ANY /*path` → proxied to `SUPARAG_LIGHTRAG_BASE_URL/*path`

## Configuration

Copy `.env.example` to `.env` and adjust values.

```env
SUPARAG_HOST=0.0.0.0
SUPARAG_PORT=8080
SUPARAG_LIGHTRAG_BASE_URL=http://127.0.0.1:9621
SUPARAG_DEFAULT_API_KEY=
SUPARAG_DEFAULT_BEARER_TOKEN=
SUPARAG_UPSTREAM_TIMEOUT_SECS=300
```

Notes:

- If your frontend already sends `Authorization` and/or `X-API-Key`, those are forwarded unchanged.
- `SUPARAG_DEFAULT_*` values are only used when client headers are missing.

## Run

```bash
cargo run
```

Server default address:

- `http://0.0.0.0:8080`

Quick checks:

- `GET /_meta/health`
- `GET /health` (proxied to upstream)

## Frontend integration

Point your frontend API base URL to this service (for example `http://localhost:8080`).

Your existing LightRAG-style client endpoints are expected to work through this proxy, including:

- `/graphs`, `/graph/label/list`, `/graph/label/popular`, `/graph/label/search`
- `/documents`, `/documents/upload`, `/documents/scan`, `/documents/reprocess_failed`, `/documents/track_status/{id}`
- `/documents/paginated`, `/documents/status_counts`, `/documents/pipeline_status`, `/documents/cancel_pipeline`
- `/query`, `/query/stream`
- `/auth-status`, `/login`

## Next hardening steps

Planned enhancements for production parity:

- request/response schema validation layer
- route-level auth middleware and RBAC
- OpenAPI spec generation
- observability (structured logs, metrics, tracing IDs)
- endpoint adapters where upstream contracts diverge

