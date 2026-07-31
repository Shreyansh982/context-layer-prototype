# API endpoints

## Purpose

The FastAPI application exposes the demo Context Layer HTTP surface. The public query contract remains `QueryRequest` to `QueryResponse`.

## Authentication

The demo uses `X-Demo-API-Key`. The authenticated principal resolves to `DEMO_USER_ID`; a body `user_id` that differs returns `403`. This is a demo boundary, not OIDC.

## Endpoints

| Method and path | Responsibility |
|---|---|
| `GET /` | Returns a basic running message. |
| `GET /health` | API process liveness. |
| `GET /ready` | Checks Neo4j, ChromaDB, Redis, and the configured primary LLM. |
| `GET /metrics` | Existing lightweight metrics response. |
| `GET /demo/session` | Returns the authenticated demo user ID. |
| `POST /query` | Stores conversation turns, retrieves context, calls the LLM service, and returns the answer. |
| `GET /dashboard` | Serves the bundled dashboard. |
| `GET /docs` | FastAPI OpenAPI UI. |

## Query contract

```json
{"user_id":"E0001","agent_id":"external_agent","query":"What is my leave balance?"}
```

`user_id` must use the `E####` format and match the principal. The response is:

```json
{"answer":"...","context_used":["conversation","memory","knowledge"]}
```

## Operational behavior and limitations

- Admission is bounded by `MAX_CONCURRENT_REQUESTS`; capacity pressure returns `503`.
- Dependency failures use the `DependencyUnavailable` handler and return `503`.
- Observations, evidence, promotion decisions, and cache intents are internal and are not exposed by this API.
