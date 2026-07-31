"""On-demand, user-scoped enterprise profile cache.

This service deliberately never materializes a workbook snapshot.  It only
refreshes fields that an authenticated user has previously requested.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.config import settings
from app.models import KnowledgeRecord, Observation
from app.services.facts import canonical_fact_service, fact_normalizer
from app.storage.neo4j import neo4j
from app.performance import timed
from app.services.observation_validation import observation_validator
from app.services.evidence import record_evidence
from app.services.promotion_policy import evaluate_shadow
from app.services.promotion_comparison import NOT_PROMOTED, PROMOTED, WRITER_FAILED, record_promotion_comparison


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EnterpriseProfileCacheService:
    source = "enterprise_on_demand"

    def enqueue_records(self, user_id: str, records: list[KnowledgeRecord], matched_fields) -> None:
        if not settings.ENTERPRISE_ON_DEMAND_CACHE_ENABLED:
            return
        requested = {(field.sheet_name, field.column_name) for field in (matched_fields or [])}
        # An explicit personal-record query has returned the entire authorized
        # record, so all non-identifier fields are considered requested.
        cache_all = not requested
        from app.services.jobs import job_queue
        for record in records:
            employee_id = str(record.row_data.get("EMP_ID", "")).strip().upper()
            if employee_id != user_id.upper():
                continue
            for column, value in record.row_data.items():
                if column == "EMP_ID" or value is None:
                    continue
                if not cache_all and (record.sheet_name, str(column)) not in requested:
                    continue
                observation = Observation.create(
                    source="enterprise_cache_request",
                    content=str(value),
                    owner_id=user_id,
                    source_record_key=f"excel:{record.sheet_name}:{employee_id}",
                    metadata={"sheet": record.sheet_name, "column": str(column)},
                )
                validated_observation = observation_validator.validate(observation)
                observation = validated_observation.observation
                record_evidence(
                    validated_observation,
                    subject=user_id.upper(),
                    predicate="HAS_ENTERPRISE_ATTRIBUTE",
                    object_value=str(value),
                    provenance={"ingress": "enterprise_cache", "sheet": record.sheet_name, "column": str(column)},
                )
                job_queue.enqueue("enterprise-cache", {
                    "user_id": user_id.upper(), "sheet": record.sheet_name,
                    "employee_id": employee_id, "column": str(column), "value": str(value),
                    "observation": observation.to_job_payload(),
                })

    @staticmethod
    def _workbook_hash() -> str:
        from app.services.knowledge import knowledge_service
        with timed("enterprise_cache.workbook_hash"):
            return hashlib.sha256(knowledge_service.workbook_path.read_bytes()).hexdigest()

    def process(self, payload: dict) -> None:
        observation_payload = payload.get("observation")
        observation = (
            Observation.model_validate(observation_payload)
            if observation_payload
            else Observation.create(
                source="enterprise_cache_request",
                content=str(payload["value"]),
                owner_id=payload["user_id"],
                source_record_key=f"excel:{payload['sheet']}:{payload['employee_id']}",
                metadata={"sheet": payload["sheet"], "column": payload["column"]},
            )
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        record_evidence(validated_observation, provenance={"ingress": "enterprise_cache_worker"})
        cache_payload = {key: value for key, value in payload.items() if key != "observation"}
        self._cache_field(**cache_payload, workbook_hash=self._workbook_hash(), observation=observation)

    def _cache_field(
        self,
        user_id: str,
        sheet: str,
        employee_id: str,
        column: str,
        value: str,
        workbook_hash: str,
        observation: Observation | None = None,
    ) -> None:
        observation = observation or Observation.create(
            source="enterprise_cache_refresh",
            content=str(value),
            owner_id=user_id,
            source_record_key=f"excel:{sheet}:{employee_id}",
            metadata={"sheet": sheet, "column": column},
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        cache_key = f"{user_id}:{sheet}:{employee_id}:{column}"
        candidate = fact_normalizer._candidate(
            user_id, "HAS_ENTERPRISE_ATTRIBUTE", value, "VALUE", "",
            {"sheet": sheet, "column": column, "cache_key": cache_key},
        )
        candidate.display_text = f"User's {column.lower().replace('_', ' ')} is {value}"
        fact_key = canonical_fact_service.fact_key(candidate)
        record_evidence(
            validated_observation,
            subject=candidate.subject_name,
            predicate=candidate.predicate,
            object_value=candidate.object_name or candidate.value,
            provenance={"writer": "enterprise_cache"},
            canonical_fact_key=fact_key,
        )
        decision = evaluate_shadow(
            validated_observation,
            subject=candidate.subject_name,
            predicate=candidate.predicate,
            object_value=candidate.object_name or candidate.value,
            canonical_fact_key=fact_key,
        )
        at = _now()
        object_key = f"value:{value.lower().replace(' ', '-')}"

        def write(tx):
            tx.run("MERGE (u:User {id:$id}) ON CREATE SET u.profile_version=0 SET u.profile_version=coalesce(u.profile_version, 0)", id=user_id).consume()
            existing = tx.run("MATCH (f:Fact {fact_key:$fact_key}) RETURN f.current AS current", fact_key=fact_key).single()
            known_current = bool(existing and existing["current"])
            tx.run("MATCH (f:Fact {cache_key:$cache_key, source_system:$source, current:true}) WHERE f.fact_key <> $fact_key OPTIONAL MATCH (:Entity)-[edge]->(:Entity) WHERE edge.fact_key=f.fact_key SET f.current=false, f.valid_to=$at, edge.current=false", cache_key=cache_key, source=self.source, fact_key=fact_key, at=at).consume()
            tx.run('''MERGE (subject:Entity {key:$subject_key})
                      ON CREATE SET subject.entity_type='PERSON', subject.display_name=$user_id
                      MERGE (object:Entity {key:$object_key})
                      ON CREATE SET object.entity_type='VALUE', object.display_name=$value
                      MERGE (fact:Fact {fact_key:$fact_key})
                      ON CREATE SET fact.predicate='HAS_ENTERPRISE_ATTRIBUTE', fact.owner_id=$user_id,
                         fact.scope='USER', fact.display_text=$display_text, fact.first_seen=$at, fact.times_observed=1
                      SET fact.current=true, fact.last_seen=$at, fact.authority_rank=100,
                         fact.source_system=$source, fact.source_record_key=$source_record_key,
                         fact.cache_key=$cache_key, fact.source_field=$column,
                         fact.source_workbook_hash=$workbook_hash, fact.last_validated_at=$at,
                         fact.cache_status='CURRENT', fact.qualifiers=$qualifiers
                      MERGE (subject)-[:SUBJECT_OF]->(fact)
                      MERGE (fact)-[:OBJECT_OF]->(object)
                      MERGE (subject)-[edge:HAS_ENTERPRISE_ATTRIBUTE {fact_key:$fact_key}]->(object)
                      SET edge.current=true, edge.first_seen=coalesce(edge.first_seen, $at)''',
                   subject_key=candidate.subject_key, object_key=object_key, user_id=user_id, value=value,
                   fact_key=fact_key, display_text=candidate.display_text, at=at, source=self.source,
                   source_record_key=f"excel:{sheet}:{employee_id}", cache_key=cache_key, column=column,
                   workbook_hash=workbook_hash, qualifiers=json.dumps(candidate.qualifiers, sort_keys=True)).consume()
            if not known_current:
                tx.run("MATCH (u:User {id:$id}) SET u.profile_version=coalesce(u.profile_version, 0)+1", id=user_id).consume()
            return not known_current

        try:
            with neo4j.get_session() as session:
                changed = session.execute_write(write)
        except Exception as error:
            record_promotion_comparison(validated_observation, decision, "enterprise_cache", WRITER_FAILED, error)
            raise
        record_promotion_comparison(
            validated_observation,
            decision,
            "enterprise_cache",
            PROMOTED if changed else NOT_PROMOTED,
        )
        if changed:
            canonical_fact_service._queue().enqueue({
                "kind": "fact",
                "fact_key": fact_key,
                "operation": "upsert",
                "origin_observation_id": observation.observation_id,
            })

    def refresh_all(self) -> int:
        """Refresh only previously requested cache keys; never import new rows."""
        # The first lazy import constructs KnowledgeService and its initial
        # workbook index. Keep that startup work distinct from reload itself.
        with timed("enterprise_cache.knowledge_service_initialization"):
            from app.services.knowledge import knowledge_service
        with timed("enterprise_cache.workbook_reload"):
            knowledge_service.reload()
        with neo4j.get_session() as session:
            with timed("enterprise_cache.cached_fact_lookup"):
                cached = list(session.run(
                    "MATCH (f:Fact {source_system:$source, current:true}) RETURN f.owner_id AS user_id, f.source_record_key AS record_key, f.source_field AS column, f.cache_key AS cache_key",
                    source=self.source))
        workbook_hash = self._workbook_hash()
        refreshed = 0
        for item in cached:
            try:
                with timed("enterprise_cache.field_refresh"):
                    _, sheet, employee_id = item["record_key"].split(":", 2)
                    dataframe = knowledge_service.loader.get_sheet(sheet)
                    if dataframe is None or "EMP_ID" not in dataframe.columns:
                        continue
                    rows = knowledge_service._accessible_rows(dataframe, item["user_id"])
                    rows = rows[rows["EMP_ID"].astype(str).str.strip().str.upper() == employee_id]
                    if rows.empty or item["column"] not in rows.columns:
                        self._close(item["cache_key"])
                        continue
                    self._cache_field(item["user_id"], sheet, employee_id, item["column"], str(rows.iloc[0][item["column"]]), workbook_hash)
                    refreshed += 1
            except Exception:
                # A single malformed cache key must not prevent other user
                # caches from being refreshed by the scheduled job.
                continue
        return refreshed

    def _close(self, cache_key: str) -> None:
        at = _now()
        observation = Observation.create(
            source="enterprise_cache_close",
            content=cache_key,
            metadata={"cache_key": cache_key},
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        record_evidence(validated_observation, provenance={"writer": "enterprise_cache_close"})
        decision = evaluate_shadow(validated_observation)

        def close(tx):
            records = list(tx.run("MATCH (f:Fact {cache_key:$cache_key, source_system:$source, current:true}) OPTIONAL MATCH (:Entity)-[edge]->(:Entity) WHERE edge.fact_key=f.fact_key SET f.current=false, f.cache_status='MISSING', f.valid_to=$at, edge.current=false RETURN f.fact_key AS key, f.owner_id AS owner_id", cache_key=cache_key, source=self.source, at=at))
            for record in records:
                tx.run("MATCH (u:User {id:$id}) SET u.profile_version=coalesce(u.profile_version, 0)+1", id=record["owner_id"]).consume()
            return records

        try:
            with neo4j.get_session() as session:
                records = session.execute_write(close)
        except Exception as error:
            record_promotion_comparison(validated_observation, decision, "enterprise_cache_close", WRITER_FAILED, error)
            raise
        record_promotion_comparison(validated_observation, decision, "enterprise_cache_close", NOT_PROMOTED)
        for record in records:
            canonical_fact_service._queue().enqueue({
                "kind": "fact",
                "fact_key": record["key"],
                "operation": "delete",
                "origin_observation_id": observation.observation_id,
            })


enterprise_profile_cache_service = EnterpriseProfileCacheService()
