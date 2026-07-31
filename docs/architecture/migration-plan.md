# Migration state and utilities

## Purpose

The repository uses additive Cypher migrations for Neo4j schema and scripts for data maintenance.

## Neo4j schema migrations

`app.bootstrap.run_migrations()` executes sorted files in `app/migrations/`.

| Migration | Current responsibility |
|---|---|
| `001_constraints.cypher` | Unique IDs and lookup indexes for users, memories, sessions, and messages. |
| `002_canonical_facts.cypher` | Identities and indexes for entities, facts, observations, and enterprise snapshots. |
| `003_profile_v2.cypher` | Profile-dimension and enterprise-cache indexes. |

All statements use `IF NOT EXISTS`, allowing repeatable bootstrap.

## Utilities

| Command | Responsibility |
|---|---|
| `python scripts/migrate_legacy_memories.py` | Reports legacy sentence memories that map to canonical candidates. |
| `python scripts/migrate_legacy_memories.py --apply` | Links recognized legacy memories without deleting their source nodes. |
| `python scripts/migrate_evidence_aggregates.py --apply` | Records the non-destructive v2 canonical-evidence cutover marker. |
| `python scripts/sync_enterprise_facts.py` | Runs the optional full enterprise sync manually. |

## Evidence-aggregate cutover

Older shadow aggregates used raw extracted values. Current canonical writers record authoritative aggregates keyed by `fact_key`.
Raw values cannot be safely backfilled because the redacted evidence ledger lacks entity-type and qualifier data needed to reconstruct a fact key.
Legacy aggregates remain until Redis TTL expiry; new writes and safe replays populate canonical ones.

## Limitations

- Scripts are operator commands and are not run automatically by Compose.
- Full enterprise synchronization remains disabled by default.
- No utility destructively deletes legacy memories or evidence.
