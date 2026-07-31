# Evidence store

## Purpose

`EvidenceStore` is a Redis-only passive ledger for validated observations. It does not write Neo4j, ChromaDB, queues, profile versions, or promotion results.

## Storage model

```text
Observation ID -> immutable Redis evidence event
Canonical fact key -> aggregate hash + source set + session set + receipt
```

The event is idempotent by `observation_id`. Canonical aggregation is
idempotent by `observation_id + fact_key`, allowing one observation to support
multiple atomic facts while preventing replay inflation.

## Interactions

- The worker records raw extraction evidence before normalization.
- Canonical fact, profile, and enterprise-cache writers add canonical evidence
  after a fact key is available.
- `EvidenceSnapshotReader` reads the canonical aggregate for shadow evaluation.

## Important decisions

Canonical aggregates use `fact_key`, not raw extracted wording. This prevents
raw values such as `USER`, aliases, or unnormalized predicates from fragmenting
the aggregate used by `PromotionPolicy`.

## Limitations and future work

- Retention is controlled by `EVIDENCE_RETENTION_DAYS`.
- Redis evidence is not an audit replacement for Neo4j fact observations.
- Existing raw aggregates are not safely backfilled; they expire by TTL.
