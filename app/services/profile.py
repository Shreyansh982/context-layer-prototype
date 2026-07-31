"""Deterministic Version 2 user-profile learning and persistence."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import redis

from app.config import settings
from app.models import MemoryFact, MemoryScope, Observation
from app.services.facts import canonical_fact_service, fact_normalizer
from app.services.predicate_registry import predicate_registry
from app.storage.neo4j import neo4j
from app.services.observation_validation import observation_validator
from app.services.evidence import record_evidence
from app.services.promotion_policy import evaluate_shadow
from app.services.promotion_comparison import NOT_PROMOTED, PROMOTED, WRITER_FAILED, record_promotion_comparison


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class ProfileSignal:
    predicate: str
    value: str
    explicit: bool = False


class ProfileNoteFilter:
    """Allow only declarative first-person information into PROFILE_NOTE."""

    _self_reference = re.compile(r"(?:^|\s)(?:i|i'm|i am|my|me|myself)(?:\s|$)", re.I)
    _temporary = re.compile(r"\b(?:this (?:answer|response|time)|just this time|for now)\b", re.I)

    def accepts(self, content: str) -> bool:
        text = content.strip()
        if not text or len(text) > 600 or "?" in text or self._temporary.search(text):
            return False
        # A profile note must be a direct self assertion, not general knowledge
        # or a statement about another person.
        return bool(self._self_reference.search(text))

    def accepts_extracted_self_fact(self, content: str) -> bool:
        """Accept the extractor's canonical ``User ...`` spelling too."""
        if self.accepts(content):
            return True
        text = content.strip()
        return bool(
            text
            and len(text) <= 600
            and "?" not in text
            and not self._temporary.search(text)
            and re.match(r"^user(?:'s)?\b", text, re.I)
        )


class DeterministicProfileParser:
    _explicit = re.compile(r"\b(?:remember|always|from now on|in the future|for future responses)\b", re.I)
    _temporary = re.compile(r"\b(?:this (?:answer|response|time)|just this time|for now)\b", re.I)
    _rules = (
        ("PREF_RESPONSE_FORMAT", "BULLETS", re.compile(r"\bbullet(?:\s+points?)?\b", re.I)),
        ("PREF_RESPONSE_FORMAT", "PARAGRAPHS", re.compile(r"\bparagraphs?\b", re.I)),
        ("PREF_RESPONSE_LENGTH", "CONCISE", re.compile(r"\b(?:concise|brief|short)\b", re.I)),
        ("PREF_RESPONSE_LENGTH", "DETAILED", re.compile(r"\b(?:detailed|in[ -]depth|comprehensive)\b", re.I)),
        ("PREF_TECHNICAL_DEPTH", "BEGINNER", re.compile(r"\b(?:beginner(?:[- ]friendly)?|simple terms)\b", re.I)),
        ("PREF_TECHNICAL_DEPTH", "ADVANCED", re.compile(r"\b(?:advanced|expert(?:-level)?)\b", re.I)),
        ("PREF_TONE", "PROFESSIONAL", re.compile(r"\bprofessional\b", re.I)),
        ("PREF_TONE", "CASUAL", re.compile(r"\bcasual\b", re.I)),
    )

    def parse(self, content: str) -> list[ProfileSignal]:
        if "?" in content or self._temporary.search(content):
            return []
        explicit = bool(self._explicit.search(content))
        seen: set[str] = set()
        result: list[ProfileSignal] = []
        for predicate, value, pattern in self._rules:
            definition = predicate_registry.definition(predicate)
            if definition and pattern.search(content) and definition.profile_dimension not in seen:
                result.append(ProfileSignal(predicate, value, explicit))
                seen.add(definition.profile_dimension)
        return result

    def might_match(self, content: str) -> bool:
        return bool(self._temporary.search(content) or self._explicit.search(content) or any(rule.search(content) for _, _, rule in self._rules) or ProfileNoteFilter()._self_reference.search(content))


class ProfileObservationStore:
    """Redis-only pre-promotion observations, one signal per session/value."""

    def __init__(self, client=None):
        self.client = client or redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    @staticmethod
    def _key(user_id: str, predicate: str, value: str) -> str:
        return f"context-layer:profile:signals:{user_id.upper()}:{predicate}:{value}"

    @staticmethod
    def _idempotency_key(message_id: str, predicate: str, value: str) -> str:
        return f"context-layer:profile:signal-id:{message_id}:{predicate}:{value}"

    @staticmethod
    def _ttl() -> int:
        return (settings.PROFILE_SIGNAL_WINDOW_DAYS + settings.PROFILE_SIGNAL_RETENTION_BUFFER_DAYS) * 86_400

    def record(self, user_id: str, session_id: str, message_id: str, signal: ProfileSignal) -> list[str] | None:
        if not self.client.set(self._idempotency_key(message_id, signal.predicate, signal.value), "1", nx=True, ex=self._ttl()):
            return None
        key = self._key(user_id, signal.predicate, signal.value)
        now = time.time()
        cutoff = now - settings.PROFILE_SIGNAL_WINDOW_DAYS * 86_400
        pipe = self.client.pipeline(transaction=True)
        pipe.zremrangebyscore(key, "-inf", cutoff)
        pipe.zadd(key, {session_id: now})
        pipe.expire(key, self._ttl())
        pipe.zrange(key, 0, -1)
        sessions = pipe.execute()[-1]
        return list(sessions)


class ProfileService:
    def __init__(self, observations: ProfileObservationStore | None = None):
        self.observations = observations or ProfileObservationStore()
        self.parser = DeterministicProfileParser()
        self.note_filter = ProfileNoteFilter()

    def should_enqueue(self, role: str, content: str) -> bool:
        return settings.PROFILE_LEARNING_ENABLED and role == "user" and self.parser.might_match(content)

    def process_message(self, payload: dict) -> None:
        if not settings.PROFILE_LEARNING_ENABLED or payload.get("role") != "user":
            return
        user_id = payload["user_id"].upper()
        content = payload["content"]
        observation = (
            Observation.model_validate(payload["observation"])
            if payload.get("observation")
            else Observation.create(
                source="profile_observation",
                content=content,
                owner_id=user_id,
                agent_id=payload.get("agent_id"),
                message_id=payload.get("message_id"),
                session_id=payload.get("session_id"),
                job_id=payload.get("job_id"),
            )
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        record_evidence(validated_observation, provenance={"ingress": "profile_observation"})
        signals = self.parser.parse(content)
        for signal in signals:
            if signal.explicit:
                self._persist_preference(user_id, signal, "profile_explicit", {
                    "message_id": payload["message_id"], "session_ids": [payload["session_id"]],
                    "policy_version": settings.PROFILE_POLICY_VERSION,
                }, observation)
                continue
            sessions = self.observations.record(user_id, payload["session_id"], payload["message_id"], signal)
            if sessions is not None and len(sessions) >= settings.PROFILE_MIN_DISTINCT_SESSIONS:
                self._persist_preference(user_id, signal, "profile_inferred", {
                    "message_id": payload["message_id"], "session_ids": sessions,
                    "signal_count": len(sessions), "policy_version": settings.PROFILE_POLICY_VERSION,
                }, observation)
        # Notes are only for self-referential statements that did not become a
        # supported preference.  General or third-party information is ignored.
        if not signals and self.note_filter.accepts(content):
            self._persist_note(user_id, content, payload, observation)

    def persist_unsupported_self_fact(
        self,
        owner_id: str,
        content: str,
        payload: dict,
        observation: Observation | None = None,
    ) -> bool:
        """Persist a rejected canonical extraction only when it is a safe self note.

        This is intentionally narrow: the caller has already established that
        the extractor identified the authenticated user as the subject.  The
        same declarative-content filter used for normal profile messages still
        rejects questions, temporary instructions, and general knowledge.
        """
        if not self.note_filter.accepts_extracted_self_fact(content):
            return False
        observation = observation or Observation.create(
            source="unsupported_self_fact",
            content=content,
            owner_id=owner_id,
            message_id=payload.get("message_id"),
            session_id=payload.get("session_id"),
            job_id=payload.get("job_id"),
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        record_evidence(validated_observation, provenance={"ingress": "unsupported_self_fact"})
        self._persist_note(owner_id.upper(), content, payload, observation)
        return True

    def _persist_preference(
        self,
        owner_id: str,
        signal: ProfileSignal,
        source: str,
        provenance: dict,
        observation: Observation | None = None,
    ) -> None:
        definition = predicate_registry.definition(signal.predicate)
        if definition is None or not definition.profile_dimension:
            return
        candidate = fact_normalizer._candidate(
            owner_id, signal.predicate, signal.value, "VALUE", "",
            {"profile_dimension": definition.profile_dimension, "profile_value": signal.value},
        )
        self._write_profile_fact(
            candidate, source, provenance, definition.profile_dimension, signal.value,
            replace=True, observation=observation,
        )

    def _persist_note(
        self,
        owner_id: str,
        content: str,
        payload: dict,
        observation: Observation | None = None,
    ) -> None:
        candidate = fact_normalizer._candidate(owner_id, "PROFILE_NOTE", content, "VALUE", content)
        self._write_profile_fact(
            candidate,
            "profile_note",
            {"message_id": payload["message_id"], "session_ids": [payload["session_id"]], "policy_version": settings.PROFILE_POLICY_VERSION},
            None,
            None,
            replace=False,
            observation=observation,
        )

    def _write_profile_fact(
        self,
        candidate,
        source: str,
        provenance: dict,
        dimension: str | None,
        value: str | None,
        replace: bool,
        observation: Observation | None = None,
    ) -> None:
        observation = observation or Observation.create(
            source=source,
            content=candidate.evidence or candidate.display_text,
            owner_id=candidate.owner_id,
            message_id=provenance.get("message_id"),
            metadata={"predicate": candidate.predicate},
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        fact_key = canonical_fact_service.fact_key(candidate)
        record_evidence(
            validated_observation,
            subject=candidate.subject_name,
            predicate=candidate.predicate,
            object_value=candidate.object_name or candidate.value,
            provenance={"writer": "profile"},
            canonical_fact_key=fact_key,
        )
        decision = evaluate_shadow(
            validated_observation,
            subject=candidate.subject_name,
            predicate=candidate.predicate,
            object_value=candidate.object_name or candidate.value,
            canonical_fact_key=fact_key,
        )
        rank = 90 if source == "profile_explicit" else 30
        relation = candidate.predicate
        observed_at = _now()
        observation_key = _hash(fact_key, source, provenance)
        object_value = candidate.value or candidate.object_name or ""
        object_key = candidate.object_key or f"value:{re.sub(r'[^a-z0-9]+', '-', object_value.lower()).strip('-')}"

        def write(tx):
            tx.run("MERGE (u:User {id:$owner_id}) ON CREATE SET u.profile_version=0 SET u.profile_version=coalesce(u.profile_version, 0)", owner_id=candidate.owner_id).consume()
            existing = list(tx.run(
                "MATCH (f:Fact {owner_id:$owner_id, current:true}) WHERE $dimension IS NOT NULL AND f.profile_dimension=$dimension RETURN f.fact_key AS key, f.authority_rank AS rank",
                owner_id=candidate.owner_id, dimension=dimension,
            )) if replace else []
            if replace and source == "profile_inferred" and any(record["rank"] >= 90 for record in existing):
                return False
            same_current = any(record["key"] == fact_key for record in existing)
            if replace and not same_current:
                tx.run("MATCH (f:Fact {owner_id:$owner_id, current:true}) WHERE f.profile_dimension=$dimension OPTIONAL MATCH (:Entity)-[edge]->(:Entity) WHERE edge.fact_key=f.fact_key SET f.current=false, f.profile_state='SUPERSEDED', f.valid_to=$at, edge.current=false", owner_id=candidate.owner_id, dimension=dimension, at=observed_at).consume()
            known = tx.run("MATCH (f:Fact {fact_key:$fact_key}) RETURN f.fact_key AS key", fact_key=fact_key).single() is not None
            tx.run(
                f'''MERGE (subject:Entity {{key:$subject_key}})
                    ON CREATE SET subject.entity_type=$subject_type, subject.display_name=$subject_name
                    MERGE (object:Entity {{key:$object_key}})
                    ON CREATE SET object.entity_type='VALUE', object.display_name=$object_value
                    MERGE (fact:Fact {{fact_key:$fact_key}})
                    ON CREATE SET fact.predicate=$predicate, fact.owner_id=$owner_id, fact.scope='USER',
                        fact.display_text=$display_text, fact.first_seen=$at, fact.times_observed=1
                    SET fact.current=true, fact.profile_state='CURRENT', fact.profile_dimension=$dimension,
                        fact.profile_value=$value, fact.authority_rank=$rank, fact.last_seen=$at,
                        fact.source_system=$source, fact.qualifiers=$qualifiers
                    MERGE (subject)-[:SUBJECT_OF]->(fact)
                    MERGE (fact)-[:OBJECT_OF]->(object)
                    MERGE (subject)-[edge:{relation} {{fact_key:$fact_key}}]->(object)
                    SET edge.current=true, edge.first_seen=coalesce(edge.first_seen, $at)
                    MERGE (o:FactObservation {{observation_key:$observation_key}})
                    ON CREATE SET o.source=$source, o.evidence='', o.provenance=$provenance, o.observed_at=$at
                    MERGE (o)-[:SUPPORTS]->(fact)''',
                subject_key=candidate.subject_key, subject_type=candidate.subject_type, subject_name=candidate.subject_name,
                object_key=object_key, object_value=object_value, fact_key=fact_key, predicate=candidate.predicate,
                owner_id=candidate.owner_id, display_text=candidate.display_text, at=observed_at,
                dimension=dimension, value=value, rank=rank, source=source,
                qualifiers=json.dumps(candidate.qualifiers, sort_keys=True), observation_key=observation_key,
                provenance=json.dumps(provenance, sort_keys=True),
            ).consume()
            changed = not known or (replace and not same_current)
            if changed:
                tx.run("MATCH (u:User {id:$owner_id}) SET u.profile_version=coalesce(u.profile_version, 0)+1", owner_id=candidate.owner_id).consume()
            return changed

        try:
            with neo4j.get_session() as session:
                changed = session.execute_write(write)
        except Exception as error:
            record_promotion_comparison(validated_observation, decision, "profile", WRITER_FAILED, error)
            raise
        record_promotion_comparison(
            validated_observation,
            decision,
            "profile",
            PROMOTED if changed else NOT_PROMOTED,
        )
        if changed:
            canonical_fact_service._queue().enqueue({
                "kind": "fact",
                "fact_key": fact_key,
                "operation": "upsert",
                "origin_observation_id": observation.observation_id,
            })

    def get_profile(self, user_id: str) -> tuple[list[MemoryFact], int]:
        with neo4j.get_session() as session:
            version = session.run("MATCH (u:User {id:$id}) RETURN coalesce(u.profile_version, 0) AS version", id=user_id.upper()).single()
            records = list(session.run(
                '''MATCH (f:Fact {owner_id:$owner_id, current:true})
                   WHERE f.profile_dimension IS NOT NULL
                   RETURN f.fact_key AS fact_key, f.display_text AS display_text, f.scope AS scope,
                       f.owner_id AS owner_id, f.first_seen AS first_seen, f.last_seen AS last_seen,
                       f.times_observed AS times_observed, f.source_record_key AS source_record_key,
                       f.predicate AS predicate, f.authority_rank AS authority_rank
                   ORDER BY f.profile_dimension''', owner_id=user_id.upper()))
        return [canonical_fact_service._memory_from_record(record) for record in records], (version["version"] if version else 0)


profile_service = ProfileService()
