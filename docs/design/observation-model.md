# Observation model

## Purpose

`Observation` is the immutable input contract for memory-producing paths. It records what was observed, not whether it should be promoted or persisted as a fact.

## Fields and responsibilities

| Field group | Responsibility |
|---|---|
| `observation_id`, `source`, `content`, `occurred_at` | Immutable identity, source type, observed content, and timestamp. |
| `owner_id`, `agent_id` | Ownership partition and caller identity. Owner IDs are normalized to uppercase. |
| `message_id`, `session_id`, `job_id` | Conversation and asynchronous-job lineage. |
| `source_record_key`, `parent_observation_id`, `metadata` | Enterprise/migration provenance, derived-observation lineage, and immutable metadata. |

## Interactions

```text
Ingress -> Observation.create() -> ObservationValidator -> EvidenceStore -> writer
```

Workers serialize observations into Redis job payloads. Derived extraction
observations inherit owner, message, session, job, and parent lineage.

## Important decisions

`ObservationValidator` is pure. It validates known sources, required owners,
parent and source-record lineage, UUID shape, normalized ownership, and metadata.
Its idempotency key is deterministic but batch-local, so it does not alter writer retry behavior.

## Limitations

- `session_id` remains optional for legacy and replay payload compatibility.
- The contract does not decide memory worthiness, confidence, promotion, or persistence.
- It does not enforce cross-request deduplication by itself.
