"""Passive Redis evidence ledger for validated observations.

This service records evidence only.  It has no dependency on Neo4j, Chroma,
queues, routing, promotion rules, or profile versioning, so it cannot change
the outcome of existing writers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import redis

from app.config import settings
from app.services.observation_validation import ValidatedObservation

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvidenceRecordResult:
    """The passive result of an idempotent evidence recording attempt."""

    observation_id: str
    evidence_key: str
    aggregate_key: str
    recorded: bool


class EvidenceStore:
    """Redis observation ledger plus raw and canonical shadow aggregates.

    Observation events are immutable and idempotent by ``observation_id``.
    Canonical fact aggregates use an additional receipt keyed by observation
    and fact key: one observation can support several atomic facts, but only
    once per fact.  This lets canonical evidence be recorded after raw
    extraction evidence without compromising retry safety.
    """

    def __init__(self, client=None):
        self.client = client or redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    @staticmethod
    def _ttl() -> int:
        return settings.EVIDENCE_RETENTION_DAYS * 86_400

    @staticmethod
    def _observation_key(observation_id: str) -> str:
        return f"context-layer:evidence:observation:{observation_id}"

    @staticmethod
    def _aggregate_key(owner_id: str | None, subject: str | None, predicate: str | None, object_value: str | None, content: str) -> str:
        material = json.dumps({
            "owner_id": owner_id or "__global__",
            "subject": subject or "",
            "predicate": predicate or "",
            "object": object_value or "",
            # Unstructured legacy observations still need a stable shadow
            # bucket without pretending that their sentence is canonical.
            "fallback_content": "" if subject or predicate or object_value else " ".join(content.lower().split()),
        }, sort_keys=True, separators=(",", ":"))
        return "context-layer:evidence:aggregate:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def canonical_aggregate_key(fact_key: str) -> str:
        """Return the authoritative aggregate key for a canonical fact."""
        return f"context-layer:evidence:canonical:{fact_key}"

    @staticmethod
    def _aggregate_observation_key(aggregate_key: str, observation_id: str) -> str:
        return f"{aggregate_key}:observation:{observation_id}"

    @staticmethod
    def _session_key(aggregate_key: str) -> str:
        return f"{aggregate_key}:sessions"

    @staticmethod
    def _source_key(aggregate_key: str) -> str:
        return f"{aggregate_key}:sources"

    def _record_aggregate(
        self,
        aggregate_key: str,
        observation,
        *,
        subject: str | None,
        predicate: str | None,
        object_value: str | None,
        confidence: float | None,
        at: str,
        canonical_fact_key: str | None,
    ) -> None:
        """Apply a previously claimed observation to exactly one aggregate."""
        self.client.hincrby(aggregate_key, "observation_count", 1)
        self.client.hsetnx(aggregate_key, "first_seen", at)
        fields = {
            "last_seen": at,
            "owner_id": observation.owner_id or "",
            "subject": subject or "",
            "predicate": predicate or "",
            "object": object_value or "",
        }
        if canonical_fact_key:
            fields.update({
                "aggregate_kind": "canonical_fact",
                "canonical_fact_key": canonical_fact_key,
            })
        self.client.hset(aggregate_key, mapping=fields)
        if confidence is not None:
            self.client.hset(aggregate_key, mapping={"last_confidence": str(confidence)})
        if observation.session_id:
            self.client.sadd(self._session_key(aggregate_key), observation.session_id)
            self.client.expire(self._session_key(aggregate_key), self._ttl())
        self.client.sadd(self._source_key(aggregate_key), observation.source)
        self.client.expire(self._source_key(aggregate_key), self._ttl())
        self.client.hset(
            aggregate_key,
            mapping={
                "distinct_session_count": str(self.client.scard(self._session_key(aggregate_key))),
                "source_count": str(self.client.scard(self._source_key(aggregate_key))),
            },
        )
        self.client.expire(aggregate_key, self._ttl())

    def record(
        self,
        validated: ValidatedObservation,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
        confidence: float | None = None,
        provenance: dict | None = None,
        canonical_fact_key: str | None = None,
    ) -> EvidenceRecordResult:
        """Record immutable evidence and, when known, canonical fact evidence.

        The supplied confidence is stored exactly as provided.  This phase
        intentionally does not calculate confidence or decide what to write.

        The first raw/pre-canonical call retains the historical one-event
        aggregate behavior.  A later canonical call may still add that same
        observation once to its fact-key aggregate through a separate receipt.
        """
        observation = validated.observation
        evidence_key = self._observation_key(observation.observation_id)
        aggregate_key = (
            self.canonical_aggregate_key(canonical_fact_key)
            if canonical_fact_key
            else self._aggregate_key(
                observation.owner_id, subject, predicate, object_value, observation.content,
            )
        )
        at = observation.occurred_at.isoformat() if observation.occurred_at else _now()
        record = {
            "observation_id": observation.observation_id,
            "parent_observation_id": observation.parent_observation_id,
            "owner_id": observation.owner_id,
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "source": observation.source,
            "session_id": observation.session_id,
            "message_id": observation.message_id,
            "timestamp": at,
            "confidence": confidence,
            "idempotency_key": validated.idempotency_key,
            "provenance": {
                "agent_id": observation.agent_id,
                "job_id": observation.job_id,
                "source_record_key": observation.source_record_key,
                "metadata": [(item.key, item.value) for item in observation.metadata],
                **(provenance or {}),
            },
        }

        # The immutable event is write-once.  Raw callers only aggregate on
        # that first write, so replaying an old event cannot inflate the legacy
        # aggregate.  Canonical callers use an observation-plus-fact receipt;
        # this deliberately remains independent of the raw event receipt.
        event_recorded = bool(
            self.client.set(evidence_key, json.dumps(record, sort_keys=True, default=str), nx=True, ex=self._ttl())
        )
        if canonical_fact_key:
            receipt_key = self._aggregate_observation_key(aggregate_key, observation.observation_id)
            aggregate_recorded = bool(self.client.set(receipt_key, "1", nx=True, ex=self._ttl()))
        else:
            aggregate_recorded = event_recorded

        if not aggregate_recorded:
            return EvidenceRecordResult(observation.observation_id, evidence_key, aggregate_key, False)

        self._record_aggregate(
            aggregate_key,
            observation,
            subject=subject,
            predicate=predicate,
            object_value=object_value,
            confidence=confidence,
            at=at,
            canonical_fact_key=canonical_fact_key,
        )
        self.client.expire(evidence_key, self._ttl())
        return EvidenceRecordResult(observation.observation_id, evidence_key, aggregate_key, True)


evidence_store = EvidenceStore()


def record_evidence(validated: ValidatedObservation, **details) -> EvidenceRecordResult | None:
    """Best-effort shadow recording that cannot alter an existing writer.

    The application already has established behaviour for Redis outages.  A
    passive diagnostic ledger must never make a memory/profile write fail, so
    evidence failures are logged and deliberately ignored.
    """
    try:
        return evidence_store.record(validated, **details)
    except Exception as error:  # pragma: no cover - depends on external Redis failure
        logger.warning(
            "Evidence recording failed; continuing existing writer: observation_id=%s error_type=%s",
            validated.observation.observation_id,
            type(error).__name__,
        )
        return None
