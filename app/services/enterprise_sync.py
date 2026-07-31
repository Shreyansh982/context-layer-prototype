"""Versioned daily workbook-to-canonical-fact synchronization."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.models import FactCandidate, MemoryScope, Observation
from app.services.facts import canonical_fact_service, fact_normalizer
from app.services.knowledge import knowledge_service
from app.storage.neo4j import neo4j
from app.services.observation_validation import observation_validator
from app.services.evidence import record_evidence


FIELD_PREDICATES = {
    "DEPT_ID": ("MEMBER_OF", "DEPARTMENT"),
    "MANAGER_ID": ("REPORTS_TO", "PERSON"),
    "GRADE": ("HAS_GRADE", "VALUE"),
    "SHIFT": ("HAS_SHIFT", "VALUE"),
    "LEAVE_BAL": ("HAS_LEAVE_BALANCE", "VALUE"),
    "SICK_BAL": ("HAS_SICK_LEAVE_BALANCE", "VALUE"),
    "LAPTOP_MODEL": ("HAS_LAPTOP_MODEL", "VALUE"),
    "VPN": ("HAS_VPN_ACCESS", "VALUE"),
    "STORAGE_GB": ("HAS_STORAGE_GB", "VALUE"),
    "PROJECT_CODE": ("HAS_PROJECT_CODE", "VALUE"),
    "STATUS": ("HAS_STATUS", "VALUE"),
    "COST_CENTER": ("HAS_COST_CENTER", "VALUE"),
    "TRAVEL_LIMIT": ("HAS_TRAVEL_LIMIT", "VALUE"),
    "MONTHLY_BUDGET": ("HAS_MONTHLY_BUDGET", "VALUE"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EnterpriseSyncService:
    """Loads a complete workbook snapshot before making it current."""

    def _snapshot_id(self) -> str:
        return hashlib.sha256(knowledge_service.workbook_path.read_bytes()).hexdigest()

    def _snapshot_complete(self, snapshot_id: str) -> bool:
        with neo4j.get_session() as session:
            record = session.run(
                "MATCH (s:EnterpriseSnapshot {snapshot_id:$id, status:'COMPLETE'}) RETURN s",
                id=snapshot_id,
            ).single()
        return record is not None

    def _mark_snapshot(self, snapshot_id: str, status: str, **fields) -> None:
        with neo4j.get_session() as session:
            session.run(
                '''MERGE (snapshot:EnterpriseSnapshot {snapshot_id:$snapshot_id})
                   SET snapshot.status=$status, snapshot.workbook_path=$workbook_path,
                       snapshot.updated_at=$updated_at, snapshot.schema_version='1',
                       snapshot.fact_count=$fact_count, snapshot.closed_fact_count=$closed_fact_count''',
                snapshot_id=snapshot_id, status=status, workbook_path=str(knowledge_service.workbook_path),
                updated_at=_now(), fact_count=fields.get("fact_count", 0),
                closed_fact_count=fields.get("closed_fact_count", 0),
            )

    def _candidate(self, employee_id: str, column: str, value) -> FactCandidate | None:
        if column == "EMP_ID" or value is None:
            return None
        predicate, object_type = FIELD_PREDICATES.get(column, ("HAS_FIELD", "VALUE"))
        object_value = str(value)
        qualifiers = {} if column in FIELD_PREDICATES else {"field": column}
        candidate = fact_normalizer._candidate(employee_id, predicate, object_value, object_type, "", qualifiers)
        # Field identity must distinguish generic HAS_FIELD values and preserve
        # exact workbook semantics without relying on English display text.
        if predicate == "HAS_FIELD":
            candidate.display_text = f"User has {column.lower().replace('_', ' ')} {object_value}"
        return candidate

    def sync(self, force: bool = False) -> dict:
        knowledge_service.reload()
        snapshot_id = self._snapshot_id()
        if not force and self._snapshot_complete(snapshot_id):
            return {"snapshot_id": snapshot_id, "status": "unchanged", "facts": 0, "closed": 0}
        self._mark_snapshot(snapshot_id, "LOADING")
        canonical_fact_service.stage_enterprise_snapshot(snapshot_id)
        snapshot_observation = Observation.create(
            source="enterprise_snapshot",
            content=snapshot_id,
            source_record_key=f"workbook:{snapshot_id}",
        )
        validated_snapshot = observation_validator.validate(snapshot_observation)
        snapshot_observation = validated_snapshot.observation
        record_evidence(validated_snapshot, provenance={"ingress": "enterprise_snapshot"})
        count = 0
        try:
            for sheet_name, dataframe in knowledge_service.loader.get_all_sheets().items():
                if "EMP_ID" not in dataframe.columns:
                    # Public sheets need an explicit business-key configuration
                    # before they can safely become canonical employee facts.
                    continue
                for _, row in dataframe.iterrows():
                    employee_id = str(row["EMP_ID"]).strip().upper()
                    if not employee_id:
                        continue
                    source_key = f"excel:{sheet_name}:{employee_id}"
                    for column, value in row.items():
                        candidate = self._candidate(employee_id, str(column), value)
                        if candidate is None:
                            continue
                        observation = Observation.create(
                            source="enterprise_excel",
                            content=str(value),
                            owner_id=employee_id,
                            source_record_key=source_key,
                            parent_observation_id=snapshot_observation.observation_id,
                            metadata={"sheet": sheet_name, "column": str(column)},
                        )
                        validated_observation = observation_validator.validate(observation)
                        observation = validated_observation.observation
                        record_evidence(
                            validated_observation,
                            subject=candidate.subject_name,
                            predicate=candidate.predicate,
                            object_value=candidate.object_name or candidate.value,
                            provenance={"ingress": "enterprise_sync", "sheet": sheet_name, "column": str(column)},
                        )
                        canonical_fact_service.upsert(
                            candidate, source="enterprise_excel",
                            provenance={
                                "snapshot_id": snapshot_id,
                                "source_record_key": source_key,
                                "sheet": sheet_name,
                                "column": str(column),
                            },
                            observation=observation,
                        )
                        count += 1
            closed = canonical_fact_service.close_missing_enterprise_facts(snapshot_id)
            published = canonical_fact_service.publish_enterprise_snapshot(snapshot_id)
            self._mark_snapshot(snapshot_id, "COMPLETE", fact_count=count, closed_fact_count=len(closed))
            return {"snapshot_id": snapshot_id, "status": "complete", "facts": published, "closed": len(closed)}
        except Exception:
            self._mark_snapshot(snapshot_id, "FAILED", fact_count=count)
            raise


enterprise_sync_service = EnterpriseSyncService()
