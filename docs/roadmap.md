# Current roadmap

## Purpose

This page records the implementation state visible in the repository. It does not activate unimplemented behavior.

## Completed

- Docker Compose runtime with API, worker, Neo4j, ChromaDB, and Redis.
- Redis conversation windows, durable queue processing, retries, and dead-letter handling.
- Canonical facts, owner-scoped entities, graph provenance, and Chroma projection recovery.
- Observation contract, deterministic validation, Redis evidence, and canonical fact-keyed aggregation.
- Shadow-only promotion policy and passive promotion comparison capture.
- Profile signals in Redis, profile notes, and profile version updates through existing writers.
- Authorized direct Excel retrieval, opt-in enterprise synchronization, Gemini primary answer generation, and Groq fallback.
- Extraction traces and local performance diagnostics.

## Current validation work

Promotion comparison records and calibration reports evaluate shadow-policy parity with current writers. Existing writers remain authoritative and no policy enforcement cutover has occurred.

## Next work within existing boundaries

- Continue collecting replay and live calibration cohorts.
- Decide whether policy enforcement is justified only after calibration supports it.
- Keep full enterprise sync optional and direct Excel lookup authoritative for the demo.

## Explicit limitations

- No lifecycle coordinator currently replaces existing writers.
- No production OIDC/JWT integration is implemented.
- No SQLite backend, Kubernetes deployment, or high-availability datastore configuration is implemented.
