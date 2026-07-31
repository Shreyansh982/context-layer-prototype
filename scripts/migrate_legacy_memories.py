"""Safely map recognized legacy sentence memories into canonical facts.

The command never deletes ``Memory`` nodes.  With ``--apply`` it marks each
recognized source node as MIGRATED and records a MIGRATED_TO relationship; all
ambiguous or unsupported sentences are retained for review.
"""

from __future__ import annotations

import argparse
import json

from app.models import ExtractedFact, Observation
from app.services.facts import canonical_fact_service, fact_normalizer
from app.storage.neo4j import neo4j


def migrate(apply: bool = False) -> dict:
    with neo4j.get_session() as session:
        rows = list(session.run(
            '''MATCH (memory:Memory {scope:'USER'})
               WHERE coalesce(memory.migration_status, 'PENDING') = 'PENDING'
               RETURN memory.id AS id, memory.content AS content, memory.owner_id AS owner_id,
                   memory.agent_id AS agent_id, memory.source AS source'''))
    result = {"scanned": len(rows), "migrated": 0, "retained": 0, "would_migrate": 0}
    for row in rows:
        if not row["owner_id"] or row.get("source") == "knowledge_lookup":
            result["retained"] += 1
            continue
        candidates = fact_normalizer.normalize(ExtractedFact(content=row["content"]), row["owner_id"])
        if not candidates:
            result["retained"] += 1
            continue
        if not apply:
            result["would_migrate"] += len(candidates)
            continue
        for candidate in candidates:
            observation = Observation.create(
                source="legacy_migration",
                content=row["content"],
                owner_id=row["owner_id"],
                agent_id=row.get("agent_id"),
                source_record_key=f"legacy-memory:{row['id']}",
            )
            fact_key = canonical_fact_service.upsert(
                candidate, source="legacy_migration",
                provenance={"legacy_memory_id": row["id"], "agent_id": row.get("agent_id")},
                observation=observation,
            )
            with neo4j.get_session() as session:
                session.run(
                    '''MATCH (memory:Memory {id:$memory_id}), (fact:Fact {fact_key:$fact_key})
                       MERGE (memory)-[:MIGRATED_TO]->(fact)
                       SET memory.migration_status='MIGRATED' ''',
                    memory_id=row["id"], fact_key=fact_key)
            result["migrated"] += 1
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write links; without it the command is a dry run")
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply), indent=2))
