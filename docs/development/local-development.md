# Local development

## Purpose

This guide runs the current laptop-oriented stack for development and debugging.

## Setup

Prerequisites are Docker Desktop with Linux containers, a sanitized `data/knowledge_base.xlsx`, and Gemini and Groq keys.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Set `GEMINI_API_KEY`, `GROQ_API_KEY`, `DEMO_API_KEY`, `DEMO_USER_ID`, and `NEO4J_PASSWORD` in `.env`.
Keep `ENABLE_ENTERPRISE_SYNC=false` unless a deliberate complete enterprise import is needed.

## Runtime responsibilities

Compose starts API, worker, Neo4j, ChromaDB, and Redis. Database ports are bound to localhost. `./logs` is mounted into API and worker containers for timing reports, comparison records, and traces.

```powershell
docker compose ps
docker compose logs -f api
docker compose logs -f worker
docker compose down
```

For local Python processes, start backing services and use localhost settings:

```powershell
python -m app.bootstrap
uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.worker
```

## Diagnostics

- `PERFORMANCE_INSTRUMENTATION_ENABLED=true` emits terminal summaries and JSONL reports.
- `DEBUG_ROUTING_CONTENT=false` is the safe default.
- `EXTRACTION_TRACE=true` writes content-bearing traces under `logs/extraction/`; see [Extraction trace mode](../extraction_trace.md).

## Limitations

The default setup uses local Docker volumes, a demo API key, and a mounted Excel file. It is not a production deployment guide.
