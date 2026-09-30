# Context Layer

Context Layer is a context and memory service for AI applications. It turns
conversation observations into retrievable context, canonical facts, profile
knowledge, and authorized enterprise context.

It is **not an AI agent**. It does not own a goal loop, choose tools, or take
actions on a user's behalf. External agents and applications call it to obtain
bounded, attributable context and to persist conversation-derived knowledge.
The repository includes a small example integration in `agent/`.

## Project snapshot

This is a static code and configuration snapshot, not a report of runtime
performance, capacity, latency, or accuracy.

| Area | Current code-derived value | Source |
|---|---|---|
| Compose topology | 5 services: API, worker, Neo4j, ChromaDB, and Redis (2 application components and 3 backing services) | `docker-compose.yml` |
| Test modules | 33 files matching `tests/test_*.py` | `tests/` |
| Request admission | 8 concurrent requests; 2-second queue timeout | `app/config.py`, `app/observability.py` |
| Input limits | 8,000 query characters; 24,000 prompt characters | `app/config.py`, `app/llm.py` |
| Context and retrieval | 8-message active conversation buffer; `TOP_K=10` | `app/config.py`, `app/services/conversation.py`, `app/context_orchestrator.py` |
| Shadow promotion policy | 3 observations; 3 distinct sessions; score threshold 0.70; 90-day recency window | `app/services/promotion_policy.py`, `app/config.py` |
| Evidence retention | 97 days | `app/config.py`, `app/services/evidence.py` |
| Profile signals | 90-day signal window plus 7-day retention buffer (97-day Redis TTL) | `app/config.py`, `app/services/profile.py` |
| Local diagnostics | Up to 2,000 in-process samples; 300-second summary interval; 14-day report retention | `app/config.py`, `app/performance.py` |
| Load-probe input | Default concurrency 40; this is a probe setting, not a capacity claim | `scripts/load_test.py` |

## Key features

- **Observation-based pipeline** - every memory-producing ingress creates an
  immutable `Observation` with ownership and lineage metadata.
- **Canonical fact extraction** - supported statements are normalized into
  typed facts such as `WORKS_AT`, `MEMBER_OF`, and `REPORTS_TO`.
- **Evidence aggregation** - Redis keeps an idempotent observation ledger and
  fact-keyed canonical evidence aggregates, including distinct session counts.
- **Shadow Promotion Policy** - evaluates evidence without changing the
  existing persistence decision.
- **Enterprise knowledge integration** - authorized direct Excel lookup remains
  authoritative; enterprise graph caching is on-demand only by default.
- **Semantic retrieval** - ChromaDB provides a recoverable vector projection of
  eligible Neo4j facts and memories.
- **Profile evolution** - deterministic profile signals are collected in Redis;
  stable preferences and accepted profile notes use the existing graph writers.
- **Promotion comparison capture** - redacted JSONL records compare the shadow
  recommendation with the current writer outcome for calibration.
- **Replay-safe processing** - observation IDs and per-fact receipts prevent
  retries from inflating evidence or canonical aggregates.

## Current architecture

```text
User
  |
  v
External Agent / Dashboard / API Client
  |
  v
Context Layer API
  |
  v
Observation
  |
  v
ObservationValidator
  |
  v
EvidenceStore
  |
  v
PromotionPolicy (shadow only)
  |
  v
Existing Writers
  |
  v
Promotion Comparison Capture
  |
  v
Neo4j / ChromaDB / Redis / Excel
```

| Stage | Responsibility |
|---|---|
| Context Layer API | Authenticates the demo principal, archives conversation turns, routes retrieval, assembles context, and generates the response. |
| Observation | Immutable record of what was observed, including owner, message, session, parent observation, and source lineage. |
| ObservationValidator | Checks structure, ownership, lineage, and idempotency metadata before a writer receives an observation. |
| EvidenceStore | Stores a Redis observation ledger and replay-safe evidence aggregates. Canonical facts aggregate by `fact_key`, not raw wording. |
| PromotionPolicy | Produces a deterministic `PROMOTE` or `COLLECT_MORE_EVIDENCE` recommendation. It is passive in the current release. |
| Existing Writers | Continue to determine current persistence outcomes for canonical facts, profile facts, enterprise cache entries, legacy memories, and vector jobs. |
| Promotion Comparison Capture | Writes redacted JSONL comparison records after a writer completes; it never alters the writer. |
| Stores and sources | Neo4j is the durable graph, ChromaDB is its semantic projection, Redis holds active windows/jobs/evidence, and Excel is the demo enterprise source. |

SQLite is **not** a current dependency or storage backend in this repository.

### Request and write flow

```text
Query -> authenticated principal -> router -> conversation/profile retrieval
      -> authorized Excel lookup -> semantic retrieval -> context package
      -> bounded prompt -> LLM response

Evicted user message -> extraction worker -> Observation -> validation
                    -> evidence -> existing writer -> vector projection
```

The request path does not use the shadow policy to gate writes. Slow extraction
and vector work run in the worker through Redis-backed jobs.

## Technology stack

| Area | Technology |
|---|---|
| API and contracts | Python, FastAPI, Uvicorn, Pydantic Settings |
| Graph system of record | Neo4j 5 and Cypher migrations |
| Semantic retrieval | ChromaDB, Sentence Transformers, PyTorch, `BAAI/bge-small-en-v1.5` |
| Shared state and jobs | Redis 7 with AOF persistence and Redis Streams |
| Enterprise source | Excel (`pandas`, `openpyxl`) |
| LLM layer | Gemini primary provider with automatic Groq fallback; Groq is also used by the extraction service |
| Runtime and diagnostics | Docker Compose, structured JSON logs, JSONL performance and promotion-comparison reports |
| Testing | pytest |

## Repository structure

```text
app/                 FastAPI API, orchestration, models, services, storage, migrations
app/services/        Conversation, facts, evidence, promotion, profile, enterprise, queue services
app/storage/         Neo4j and ChromaDB adapters
agent/               Example external-agent integration
dashboard/           Lightweight browser dashboard served by the API
data/                Sanitized demo workbook and optional policy seed data
scripts/             Sync, replay, migration, load-test, and calibration utilities
docs/                Developer documentation, including extraction trace guidance
tests/               Unit and regression tests
logs/                Host-mounted local diagnostics and traces (not source data)
```

## Getting started

### Prerequisites

- Docker Desktop with Linux containers enabled.
- A sanitized workbook at `data/knowledge_base.xlsx` containing the demo user.
- Gemini and Groq API keys. Gemini is the default answer provider; Groq is the
  automatic fallback and is required for extraction.
- Python 3.11+ only if you intend to run tooling outside Docker.

### Configure the environment

```powershell
Copy-Item .env.example .env
```

Set at least the following in `.env`:

```env
PRIMARY_LLM=gemini
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
DEMO_API_KEY=choose-a-long-random-value
DEMO_USER_ID=E0001
NEO4J_PASSWORD=choose-a-strong-password
```

`DEMO_API_KEY` is an application credential, not an LLM key. `DEMO_USER_ID`
must correspond to an employee record in the workbook.

For development, the important defaults are:

```env
ENABLE_ENTERPRISE_SYNC=false
ENABLE_COMPANY_POLICY_SEED=false
```

Direct Excel lookup still works with enterprise sync disabled. No complete
enterprise workbook import occurs unless `ENABLE_ENTERPRISE_SYNC=true` or the
manual sync command is run.

### Start with Docker Compose

```powershell
docker compose up --build
```

This starts the API, worker, Neo4j, ChromaDB, and Redis. The API is available
at <http://127.0.0.1:8000> and the dashboard at
<http://127.0.0.1:8000/dashboard>.

Useful endpoints:

- `GET /health` - process liveness
- `GET /ready` - dependency readiness
- `GET /docs` - OpenAPI UI
- `GET /metrics` - existing lightweight metrics endpoint

### Run the API and worker separately

With the data services already running, start either runtime explicitly:

```powershell
docker compose up api
docker compose up worker
```

For a local Python workflow, configure `.env` with localhost service addresses,
then run:

```powershell
python -m app.bootstrap
uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.worker
```

### Call the API

```powershell
$headers = @{ "X-Demo-API-Key" = "your-demo-key" }
$body = @{
  user_id = "E0001"
  agent_id = "manual_demo"
  query = "What is my leave balance?"
} | ConvertTo-Json

Invoke-RestMethod http://127.0.0.1:8000/query -Method Post `
  -Headers $headers -ContentType "application/json" -Body $body
```

The API validates that `user_id` matches the authenticated demo principal;
clients cannot select another employee's private enterprise data.

### Run tests

After building the images:

```powershell
docker compose run --rm --no-deps --entrypoint python worker -m pytest -q
```

### Useful maintenance commands

```powershell
# Manually synchronize the enterprise workbook when intentionally required.
docker compose exec worker python scripts/sync_enterprise_facts.py

# Inspect/apply the non-destructive canonical evidence-aggregate cutover.
docker compose exec worker python scripts/migrate_evidence_aggregates.py
docker compose exec worker python scripts/migrate_evidence_aggregates.py --apply

# Stop services while preserving volumes.
docker compose down
```

## Current project status

The repository is in the **calibration and validation phase** of its memory
lifecycle evolution.

- The Promotion Policy is **shadow-only**.
- Existing writers remain authoritative for persistence outcomes.
- No policy enforcement or behavioral promotion cutover has occurred.
- Promotion comparison capture is enabled by default for calibration and writes
  redacted local JSONL records under `logs/`.
- Canonical evidence aggregates are now keyed by canonical fact identity;
  legacy raw aggregates are retained until their Redis TTL expires.
- Automatic full enterprise synchronization is disabled by default. Direct
  Excel retrieval remains authoritative, and on-demand enterprise caching is
  retained for authorized interaction-driven fields.

## Roadmap

### Completed

- Redis-backed active conversation state and durable worker jobs.
- Docker Compose demo stack with Neo4j, ChromaDB, Redis, API, and worker.
- Canonical facts, owner-scoped entities, provenance observations, and vector
  projection recovery.
- Observation contract, validation, passive evidence collection, and shadow
  promotion evaluation.
- Profile signal collection, configurable learning settings, and profile
  versioning through existing writers.
- Developer extraction traces, request/job performance diagnostics, and
  promotion comparison capture.
- Canonical fact-keyed evidence aggregation with replay-safe receipts.

### Next

- Collect and analyze the replay and live calibration cohorts.
- Validate promotion-policy parity and decide whether a controlled behavioral
  cutover is justified.
- Consolidate write paths behind the planned lifecycle coordinator only after
  calibration supports that change.
- Evolve demo authentication to OIDC/JWT and scale API/worker replicas behind a
  reverse proxy when deployment requirements demand it.

## Security and data handling

Use only sanitized workbooks and demo data. Keep `.env`, API keys, local logs,
and extraction traces out of source control. `EXTRACTION_TRACE=true` includes
user content and is intended only for local debugging. For detailed trace
usage, see [docs/extraction_trace.md](docs/extraction_trace.md).
