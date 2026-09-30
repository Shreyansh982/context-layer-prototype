# Architecture

## Purpose

Context Layer is a context, memory, and retrieval service for AI applications.
It is not an AI agent: it does not own a goal loop or execute tools. Clients
and external agents call its API to persist conversations and retrieve bounded,
owner-scoped context.

## Runtime components

```text
Client / external agent
          |
          v
      FastAPI API --------------------> Excel workbook
          |                                  |
          |                                  v
 Redis active conversation window    authorized direct lookup
          |
          +--> Redis Streams --> worker --> Neo4j facts --> ChromaDB projection
          |
          +--> Redis evidence and profile-signal state
```

| Component | Current responsibility |
|---|---|
| FastAPI | Demo-key authentication, request admission, routing, context assembly, prompt construction, and answer delivery. |
| Redis | Active conversation windows, Redis Stream work queues, evidence aggregates, and profile-learning signals. |
| Neo4j | Durable users, sessions, messages, canonical facts, fact observations, profiles, and legacy memories. |
| ChromaDB | Recoverable semantic projection for facts and legacy memories. |
| Excel | Authoritative demo enterprise source for authorized employee fields. |
| Worker | Extraction, deferred context-write intents, profile handling, enterprise-cache jobs, vector projection, and optional enterprise sync. |

Gemini is the configurable primary answer provider. Groq is the automatic
answer fallback and is used by the current extraction service.

## Important implementation decisions

- Neo4j is the durable system of record; ChromaDB can be rebuilt from graph records.
- Read-only context assembly returns internal cache/refresh intents rather than executing them. The API queues distinct intents only after assembly; worker handlers invoke the existing writers.
- `ENABLE_ENTERPRISE_SYNC=false` is the development default; direct Excel lookup remains available.
- User-facing identity comes from the authenticated principal and must match request `user_id`.
- The current demo stack contains no SQLite component.

## Limitations

- Demo authentication is an API key and one configured principal, not OIDC.
- Excel is loaded as the enterprise source; it is not a transactional database.
- API and worker run as single containers in the default Compose deployment.

## Related documents

- [Pipeline](pipeline.md)
- [Canonical facts](../design/canonical-facts.md)
- [Local development](../development/local-development.md)
