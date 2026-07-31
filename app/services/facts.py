"""Canonical fact normalization, persistence, and retrieval.

Sentence memories remain available as a legacy compatibility path.  Durable
facts extracted from new conversations use this module instead: their identity
is subject + predicate + object/value + qualifiers, never their wording.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.models import ExtractedFact, FactCandidate, MemoryFact, MemoryScope, Observation
from app.storage.neo4j import neo4j
from app.services.predicate_registry import predicate_registry
from app.extraction_trace import current_trace_id, emit as trace_emit, enabled as trace_enabled
from app.services.observation_validation import observation_validator
from app.services.evidence import record_evidence
from app.services.promotion_policy import evaluate_shadow
from app.services.promotion_comparison import PROMOTED, WRITER_FAILED, record_promotion_comparison

# Compatibility export for existing callers and tests.  Predicate governance
# itself lives behind the PredicateRegistry interface.
PREDICATES = predicate_registry.names

ENTERPRISE_OWNED_SINGLE_VALUE = {
    "WORKS_AT", "REPORTS_TO", "HAS_GRADE", "HAS_SHIFT", "HAS_LEAVE_BALANCE",
    "HAS_SICK_LEAVE_BALANCE", "HAS_LAPTOP_MODEL", "HAS_VPN_ACCESS",
    "HAS_STORAGE_GB", "HAS_PROJECT_CODE", "HAS_STATUS", "HAS_COST_CENTER",
    "HAS_TRAVEL_LIMIT", "HAS_MONTHLY_BUDGET",
}

ORGANIZATION_ALIASES = {
    "jindal steel": ("jindal-steel", "Jindal Steel Ltd"),
    "jindal steel ltd": ("jindal-steel", "Jindal Steel Ltd"),
    "jindal steel limited": ("jindal-steel", "Jindal Steel Ltd"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip(" .,!;:"))


def _stable_hash(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FactNormalizer:
    """Deterministic resolver for the supported personal-fact vocabulary."""

    @staticmethod
    def _is_authenticated_user_reference(value: str, owner_id: str) -> bool:
        """Recognize first-person aliases and the owner's enterprise ID.

        The authenticated owner is the only valid representation of the
        speaker.  In particular, an extractor must never turn ``I`` or the
        owner's employee ID into a second Person node.
        """
        normalized = _clean(value).upper()
        owner = owner_id.strip().upper()
        if normalized in {"USER", "I", "ME", "MY", "MINE", "MYSELF"}:
            return True
        return bool(re.fullmatch(rf"(?:EMPLOYEE|EMP|EMP_ID)?\s*[:#-]?\s*{re.escape(owner)}", normalized))

    def _entity(self, value: str, entity_type: str, owner_id: str) -> tuple[str, str, str]:
        value = _clean(value)
        kind = (entity_type or "ENTITY").upper()
        if kind in {"PERSON", "USER", "EMPLOYEE", "ENTITY"}:
            if self._is_authenticated_user_reference(value, owner_id):
                return f"person:{owner_id.upper()}", "PERSON", owner_id.upper()
        if kind in {"PERSON", "USER", "EMPLOYEE"}:
            # A name without a directory identifier is deliberately scoped to
            # the user's social graph.  It must not merge globally by name.
            return f"person:{owner_id.upper()}:{_slug(value)}", "PERSON", value
        if kind in {"ORGANIZATION", "COMPANY"}:
            alias = ORGANIZATION_ALIASES.get(value.lower())
            if alias:
                return f"organization:{alias[0]}", "ORGANIZATION", alias[1]
            return f"organization:{_slug(value)}", "ORGANIZATION", value
        if kind in {"TEAM", "DEPARTMENT"}:
            display = value
            if kind == "TEAM" and not value.lower().endswith("team"):
                display = f"{value} Team"
            return f"{kind.lower()}:{_slug(display)}", kind, display
        if kind in {"DEVICE", "PROJECT"}:
            return f"{kind.lower()}:{_slug(value)}", kind, value
        return f"entity:{_slug(value)}", kind, value

    @staticmethod
    def _is_self_external_relationship(candidate: FactCandidate) -> bool:
        """Never create a colleague/manager edge from a user to themselves."""
        return bool(
            candidate.object_key
            and candidate.object_key == candidate.subject_key
            and predicate_registry.definition(candidate.predicate)
            and predicate_registry.definition(candidate.predicate).storage == "entity"
        )

    @staticmethod
    def _predicate(value: str | None) -> str | None:
        definition = predicate_registry.resolve(value)
        return definition.name if definition else None

    def _candidate(
        self, owner_id: str, predicate: str, object_value: str, object_type: str | None,
        evidence: str, qualifiers: dict[str, Any] | None = None,
        subject_value: str = "USER", subject_type: str = "PERSON",
    ) -> FactCandidate:
        # The authenticated owner partitions every personal graph, but it is
        # not necessarily the subject of every extracted assertion.  For
        # example, "My manager is Gyan Gupta. He is VP of IT." produces one
        # fact about the owner and one fact about Gyan, both owner-scoped.
        subject_key, resolved_subject_type, subject_name = self._entity(
            subject_value, subject_type, owner_id,
        )
        definition = predicate_registry.definition(predicate)
        if definition and definition.storage == "entity":
            object_key, resolved_type, object_name = self._entity(object_value, object_type or "ENTITY", owner_id)
            value = None
        else:
            object_key, resolved_type, object_name, value = None, None, None, _clean(object_value)
        qualifier_data = qualifiers or {}
        display_subject = "User" if subject_key == f"person:{owner_id.upper()}" else subject_name
        display = self.display_text(display_subject, predicate, object_name or value or "", qualifier_data)
        return FactCandidate(
            subject_key=subject_key, subject_type=resolved_subject_type, subject_name=subject_name,
            predicate=predicate, object_key=object_key, object_type=resolved_type,
            object_name=object_name, value=value, qualifiers=qualifier_data,
            owner_id=owner_id.upper(), display_text=display, evidence=evidence,
        )

    @staticmethod
    def display_text(subject: str, predicate: str, object_value: str, qualifiers: dict[str, Any]) -> str:
        text = {
            "WORKS_AT": f"{subject} works at {object_value}",
            "MEMBER_OF": f"{subject} is a member of {object_value}",
            "REPORTS_TO": f"{subject} reports to {object_value}",
            "COLLEAGUE": f"{subject}'s colleague is {object_value}",
            "HAS_NAME": f"{subject}'s name is {object_value}",
            "PREFERS": f"{subject} prefers {object_value}",
        }.get(predicate, f"{subject} {predicate.lower().replace('_', ' ')} {object_value}")
        return text + (f" ({qualifiers})" if qualifiers else "")

    def normalize(self, fact: ExtractedFact, owner_id: str) -> list[FactCandidate]:
        """Return atomic supported facts; unsupported text stays on legacy path."""
        evidence = _clean(fact.content)
        predicate = self._predicate(fact.predicate)
        if predicate and fact.object:
            candidate = self._candidate(
                owner_id, predicate, fact.object, fact.object_type, evidence,
                fact.qualifiers, fact.subject, fact.subject_type,
            )
            return [] if self._is_self_external_relationship(candidate) else [candidate]
        return self._parse_sentence(evidence, owner_id)

    def _parse_sentence(self, content: str, owner_id: str) -> list[FactCandidate]:
        if not content:
            return []
        patterns: list[tuple[str, str, str]] = [
            (r"(?:user\s+)?(?:works?|work)\s+(?:at|for)\s+(.+)$", "WORKS_AT", "ORGANIZATION"),
            (r"user\s+(?:is\s+(?:an?\s+)?intern\s+in|works?\s+in|is\s+in)\s+(?:the\s+)?(.+?)(?:\s+team)?(?:\s+at\s+(.+))?$", "MEMBER_OF", "TEAM"),
            (r"user(?:'s)?\s+(?:manager(?:'s\s+name)?\s+is|reports?\s+to)\s+(.+?)(?:,.*)?$", "REPORTS_TO", "PERSON"),
            (r"user(?:'s)?\s+colleague\s+is\s+(.+)$", "COLLEAGUE", "PERSON"),
            (r"user(?:'s)?\s+name\s+is\s+(.+)$", "HAS_NAME", "VALUE"),
            (r"user\s+(?:prefers?|likes?)\s+(.+)$", "PREFERS", "VALUE"),
        ]
        candidates: list[FactCandidate] = []
        plural = re.search(
            r"(?:user\s+has\s+(?:two\s+)?(?:fellow|colleague)\s+interns?|"
            r"[A-Z][a-z]+\s+and\s+[A-Z][a-z]+\s+are\s+(?:colleague|fellow)\s+interns?)",
            content, re.I,
        )
        if plural:
            names = re.findall(r"\b([A-Z][a-z]+)\b", content)
            ignored = {"User", "Fellow", "Colleague", "Intern", "Interns", "And"}
            for name in dict.fromkeys(name for name in names if name not in ignored):
                candidates.append(self._candidate(owner_id, "COLLEAGUE", name, "PERSON", content))
            if candidates:
                return [candidate for candidate in candidates if not self._is_self_external_relationship(candidate)]
        for pattern, predicate, object_type in patterns:
            match = re.fullmatch(pattern, content, re.I)
            if not match:
                continue
            candidates.append(self._candidate(owner_id, predicate, match.group(1), object_type, content))
            # A compound membership statement also contains organization fact.
            if predicate == "MEMBER_OF" and match.lastindex and match.lastindex >= 2 and match.group(2):
                candidates.append(self._candidate(owner_id, "WORKS_AT", match.group(2), "ORGANIZATION", content))
            return [candidate for candidate in candidates if not self._is_self_external_relationship(candidate)]
        return []


class CanonicalFactService:
    """Neo4j source of truth for facts; Chroma is a recoverable projection."""

    def __init__(self, collection=None, embedder=None, queue=None):
        self.collection = collection
        self.embedder = embedder
        self.queue = queue

    @staticmethod
    def fact_key(candidate: FactCandidate) -> str:
        return _stable_hash(
            "demo", candidate.subject_key, candidate.predicate,
            candidate.object_key or candidate.value,
            {key: candidate.qualifiers[key] for key in sorted(candidate.qualifiers)},
        )

    def _queue(self):
        if self.queue is None:
            from app.services.jobs import job_queue
            self.queue = job_queue
        return self.queue

    def _collection(self):
        if self.collection is None:
            from app.storage.chroma import chroma
            self.collection = chroma.get_fact_collection()
        return self.collection

    def _embed(self, text: str) -> list[float]:
        if self.embedder is None:
            from app.services.embedding import embedding_service
            self.embedder = embedding_service.embed
        return self.embedder(text)

    @staticmethod
    def _authority_rank(source: str) -> int:
        return 100 if source.startswith("enterprise") else 20

    def upsert(
        self,
        candidate: FactCandidate,
        source: str,
        provenance: dict[str, Any] | None = None,
        observation: Observation | None = None,
    ) -> str:
        """Idempotently persist one canonical fact and one source observation."""
        if predicate_registry.definition(candidate.predicate) is None:
            raise ValueError(f"Unsupported canonical predicate: {candidate.predicate}")
        provenance = provenance or {}
        # Preserve the existing persistence contract while ensuring every
        # canonical writer has one common, immutable input record available.
        observation = observation or Observation.create(
            source=source,
            content=candidate.evidence or candidate.display_text,
            owner_id=candidate.owner_id,
            job_id=provenance.get("job_id"),
            source_record_key=provenance.get("source_record_key"),
            metadata={"predicate": candidate.predicate},
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        fact_key = self.fact_key(candidate)
        record_evidence(
            validated_observation,
            subject=candidate.subject_name,
            predicate=candidate.predicate,
            object_value=candidate.object_name or candidate.value,
            provenance={"writer": "canonical_fact"},
            canonical_fact_key=fact_key,
        )
        decision = evaluate_shadow(
            validated_observation,
            subject=candidate.subject_name,
            predicate=candidate.predicate,
            object_value=candidate.object_name or candidate.value,
            canonical_fact_key=fact_key,
        )
        observed_at = _now()
        evidence_hash = _stable_hash(candidate.evidence, provenance.get("job_id"), provenance.get("source_record_key"))
        observation_key = _stable_hash(fact_key, source, evidence_hash)
        object_key = candidate.object_key or f"value:{_slug(candidate.value or '')}"
        object_type = candidate.object_type or "VALUE"
        object_name = candidate.object_name or candidate.value or ""
        rank = self._authority_rank(source)
        make_current = not source.startswith("enterprise")
        # Predicate comes from the closed PREDICATES vocabulary above; it is
        # safe to interpolate as a relationship type for graph traversal.
        relation = candidate.predicate
        cypher = f'''
                MERGE (subject:Entity {{key:$subject_key}})
                ON CREATE SET subject.entity_type=$subject_type, subject.display_name=$subject_name
                MERGE (object:Entity {{key:$object_key}})
                ON CREATE SET object.entity_type=$object_type, object.display_name=$object_name
                MERGE (fact:Fact {{fact_key:$fact_key}})
                ON CREATE SET fact.predicate=$predicate, fact.owner_id=$owner_id,
                    fact.scope=$scope, fact.display_text=$display_text,
                    fact.first_seen=$observed_at, fact.current=$make_current,
                    fact.authority_rank=$authority_rank, fact.times_observed=1,
                    fact.source_system=$source_system, fact.source_record_key=$source_record_key,
                    fact.last_snapshot=$snapshot_id, fact.qualifiers=$qualifiers
                ON MATCH SET fact.last_seen=$observed_at,
                    fact.current=CASE WHEN $make_current THEN true ELSE fact.current END,
                    fact.display_text=$display_text,
                    fact.authority_rank=CASE WHEN coalesce(fact.authority_rank, 0) > $authority_rank THEN fact.authority_rank ELSE $authority_rank END,
                    fact.source_system=CASE WHEN $authority_rank >= coalesce(fact.authority_rank, 0) THEN $source_system ELSE fact.source_system END,
                    fact.source_record_key=CASE WHEN $authority_rank >= coalesce(fact.authority_rank, 0) THEN $source_record_key ELSE fact.source_record_key END,
                    fact.last_snapshot=CASE WHEN $snapshot_id IS NULL THEN fact.last_snapshot ELSE $snapshot_id END
                MERGE (subject)-[:SUBJECT_OF]->(fact)
                MERGE (fact)-[:OBJECT_OF]->(object)
                MERGE (subject)-[edge:{relation} {{fact_key:$fact_key}}]->(object)
                ON CREATE SET edge.current=$make_current, edge.first_seen=$observed_at
                ON MATCH SET edge.current=CASE WHEN $make_current THEN true ELSE edge.current END
                MERGE (observation:FactObservation {{observation_key:$observation_key}})
                ON CREATE SET observation.source=$source, observation.evidence=$evidence,
                    observation.provenance=$provenance, observation.observed_at=$observed_at
                MERGE (observation)-[:SUPPORTS]->(fact)
                '''
        try:
            with neo4j.get_session() as session:
                result = session.run(
                    cypher,
                    subject_key=candidate.subject_key, subject_type=candidate.subject_type, subject_name=candidate.subject_name,
                    object_key=object_key, object_type=object_type, object_name=object_name,
                    fact_key=fact_key, predicate=candidate.predicate, owner_id=candidate.owner_id,
                    scope=candidate.scope.value, display_text=candidate.display_text,
                    observed_at=observed_at, authority_rank=rank, source_system=source,
                    make_current=make_current,
                    source_record_key=provenance.get("source_record_key"), snapshot_id=provenance.get("snapshot_id"),
                    qualifiers=json.dumps(candidate.qualifiers, sort_keys=True), observation_key=observation_key,
                    evidence=candidate.evidence, provenance=json.dumps(provenance, sort_keys=True), source=source,
                )
                if trace_enabled() and current_trace_id():
                    summary = result.consume()
                    counters = summary.counters
                    trace_emit("graph_write_details", cypher=cypher.strip(), fact_key=fact_key,
                               merged_nodes=[candidate.subject_key, object_key, fact_key, observation_key],
                               relationships=["SUBJECT_OF", "OBJECT_OF", relation, "SUPPORTS"],
                               nodes_created=counters.nodes_created, relationships_created=counters.relationships_created,
                               properties_set=counters.properties_set)
        except Exception as error:
            record_promotion_comparison(validated_observation, decision, "canonical_fact", WRITER_FAILED, error)
            raise
        record_promotion_comparison(validated_observation, decision, "canonical_fact", PROMOTED)
        vector_payload = {
            "kind": "fact", "fact_key": fact_key, "operation": "upsert",
            "origin_observation_id": observation.observation_id,
        }
        if trace_enabled() and current_trace_id():
            vector_payload["trace_id"] = current_trace_id()
        self._queue().enqueue("vector-projection", vector_payload)
        return fact_key

    @staticmethod
    def _memory_from_record(record) -> MemoryFact:
        return MemoryFact(
            id=record["fact_key"], content=record["display_text"], scope=MemoryScope(record["scope"]),
            owner_id=record.get("owner_id"), agent_id=None,
            confidence=min(1.0, 0.5 + min(record.get("times_observed", 1), 5) * 0.1),
            timestamp=datetime.fromisoformat(record.get("last_seen") or record["first_seen"]),
            source="canonical_fact", knowledge_reference=record.get("source_record_key"),
        )

    def _select_authoritative(self, records) -> list[MemoryFact]:
        best: dict[str, Any] = {}
        result = []
        for record in records:
            predicate = record["predicate"]
            if predicate not in ENTERPRISE_OWNED_SINGLE_VALUE:
                result.append(self._memory_from_record(record))
                continue
            existing = best.get(predicate)
            if existing is None or record.get("authority_rank", 0) > existing.get("authority_rank", 0):
                best[predicate] = record
        return result + [self._memory_from_record(record) for record in best.values()]

    def get_user_facts(self, owner_id: str) -> list[MemoryFact]:
        """Return current facts whose subject is the authenticated user."""
        with neo4j.get_session() as session:
            records = list(session.run(
                '''
                MATCH (:Entity {key:$subject_key})-[:SUBJECT_OF]->(fact:Fact {current:true})
                RETURN fact.fact_key AS fact_key, fact.display_text AS display_text, fact.scope AS scope,
                    fact.owner_id AS owner_id, fact.first_seen AS first_seen, fact.last_seen AS last_seen,
                    fact.times_observed AS times_observed, fact.source_record_key AS source_record_key,
                    fact.predicate AS predicate, fact.authority_rank AS authority_rank
                ORDER BY fact.last_seen DESC
                ''', subject_key=f"person:{owner_id.upper()}"))
        return self._select_authoritative(records)

    def get_owner_facts(self, owner_id: str) -> list[MemoryFact]:
        """Return all current facts in one owner's isolated profile graph."""
        with neo4j.get_session() as session:
            records = list(session.run(
                '''
                MATCH (fact:Fact {owner_id:$owner_id, current:true})
                RETURN fact.fact_key AS fact_key, fact.display_text AS display_text, fact.scope AS scope,
                    fact.owner_id AS owner_id, fact.first_seen AS first_seen, fact.last_seen AS last_seen,
                    fact.times_observed AS times_observed, fact.source_record_key AS source_record_key,
                    fact.predicate AS predicate, fact.authority_rank AS authority_rank
                ORDER BY fact.last_seen DESC
                ''', owner_id=owner_id.upper()))
        return self._select_authoritative(records)

    def get_fact(self, fact_key: str) -> MemoryFact | None:
        with neo4j.get_session() as session:
            record = session.run(
                '''MATCH (fact:Fact {fact_key:$fact_key, current:true})
                   RETURN fact.fact_key AS fact_key, fact.display_text AS display_text, fact.scope AS scope,
                       fact.owner_id AS owner_id, fact.first_seen AS first_seen, fact.last_seen AS last_seen,
                       fact.times_observed AS times_observed, fact.source_record_key AS source_record_key,
                       fact.predicate AS predicate, fact.authority_rank AS authority_rank''',
                fact_key=fact_key).single()
        return self._memory_from_record(record) if record else None

    def search(self, query: str, owner_id: str, top_k: int) -> list[MemoryFact]:
        """Semantic retrieval over the recoverable canonical-fact projection."""
        collection = self._collection()
        count = collection.count()
        if count == 0:
            return []
        results = collection.query(
            query_embeddings=[self._embed(query)], n_results=min(top_k, count),
            where={"owner_id": owner_id.upper()},
        )
        facts = []
        for fact_key in (results.get("ids") or [[]])[0]:
            fact = self.get_fact(fact_key)
            if fact and fact.owner_id == owner_id.upper():
                facts.append(fact)
        return facts

    def close_missing_enterprise_facts(self, snapshot_id: str) -> list[str]:
        """Close, rather than delete, facts no longer present in a completed snapshot."""
        closed_at = _now()
        with neo4j.get_session() as session:
            records = list(session.run(
                '''
                MATCH (fact:Fact {current:true, source_system:'enterprise_excel'})
                WHERE fact.last_snapshot <> $snapshot_id
                SET fact.current=false, fact.valid_to=$closed_at
                RETURN fact.fact_key AS fact_key
                ''', snapshot_id=snapshot_id, closed_at=closed_at))
            fact_keys = [record["fact_key"] for record in records]
            if fact_keys:
                session.run(
                    '''MATCH (:Entity)-[edge]->(:Entity)
                       WHERE edge.fact_key IN $fact_keys
                       SET edge.current=false''', fact_keys=fact_keys)
        for fact_key in fact_keys:
            self._queue().enqueue("vector-projection", {"kind": "fact", "fact_key": fact_key, "operation": "delete"})
        return fact_keys

    def publish_enterprise_snapshot(self, snapshot_id: str) -> int:
        """Make a fully imported snapshot visible in one final graph update."""
        with neo4j.get_session() as session:
            records = list(session.run(
                '''MATCH (fact:Fact {source_system:'enterprise_excel', last_snapshot:$snapshot_id})
                   SET fact.current=true, fact.valid_to=null
                   RETURN fact.fact_key AS fact_key''', snapshot_id=snapshot_id))
            fact_keys = [record["fact_key"] for record in records]
            if fact_keys:
                session.run(
                    '''MATCH (:Entity)-[edge]->(:Entity)
                       WHERE edge.fact_key IN $fact_keys
                       SET edge.current=true''', fact_keys=fact_keys)
        return len(fact_keys)

    def stage_enterprise_snapshot(self, snapshot_id: str) -> None:
        """Hide a retry of an incomplete snapshot until it is fully published."""
        with neo4j.get_session() as session:
            records = list(session.run(
                '''MATCH (fact:Fact {source_system:'enterprise_excel', last_snapshot:$snapshot_id})
                   SET fact.current=false
                   RETURN fact.fact_key AS fact_key''', snapshot_id=snapshot_id))
            fact_keys = [record["fact_key"] for record in records]
            if fact_keys:
                session.run(
                    '''MATCH (:Entity)-[edge]->(:Entity)
                       WHERE edge.fact_key IN $fact_keys
                       SET edge.current=false''', fact_keys=fact_keys)

    def project_vector(self, fact_key: str, operation: str = "upsert") -> None:
        collection = self._collection()
        if operation == "delete":
            collection.delete(ids=[fact_key])
            return
        fact = self.get_fact(fact_key)
        if fact is None:
            collection.delete(ids=[fact_key])
            return
        collection.upsert(
            ids=[fact.id], documents=[fact.content], embeddings=[self._embed(fact.content)],
            metadatas=[{"owner_id": fact.owner_id or "", "scope": fact.scope.value, "source": fact.source}],
        )

    def enqueue_pending_vector_projections(self) -> int:
        with neo4j.get_session() as session:
            ids = [row["fact_key"] for row in session.run(
                "MATCH (f:Fact {current:true}) RETURN f.fact_key AS fact_key")]
        for fact_key in ids:
            self._queue().enqueue("vector-projection", {"kind": "fact", "fact_key": fact_key, "operation": "upsert"})
        return len(ids)


fact_normalizer = FactNormalizer()
canonical_fact_service = CanonicalFactService()
