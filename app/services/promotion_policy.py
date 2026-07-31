"""Pure, shadow-only promotion evaluation over Phase 3A evidence."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from app.config import settings
from app.models import Observation
from app.services.confidence import clamp, source_certainty
from app.services.evidence import EvidenceStore, evidence_store
from app.services.observation_validation import ValidatedObservation

logger = logging.getLogger(__name__)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class EvidenceSummary:
    """Read-only projection of the Redis shadow aggregate."""

    observation_count: int = 0
    distinct_session_count: int = 0
    source_count: int = 0
    source_types: tuple[str, ...] = ()
    first_seen: str | None = None
    last_seen: str | None = None
    last_confidence: float | None = None
    source_record_key: str | None = None


@dataclass(frozen=True)
class PromotionDecision:
    """A deterministic recommendation only; it has no write capability."""

    eligible: bool
    recommended_action: str
    reason: str
    evidence_summary: EvidenceSummary
    score: float
    threshold: float
    evaluation_timestamp: str

    def to_log_record(self) -> dict:
        return asdict(self)


class EvidenceSnapshotReader:
    """Read evidence aggregates without mutating the Phase 3A Redis ledger."""

    def __init__(self, store: EvidenceStore | None = None):
        self.store = store or evidence_store

    def read(
        self,
        observation: Observation,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object_value: str | None = None,
        canonical_fact_key: str | None = None,
    ) -> EvidenceSummary:
        aggregate_key = (
            self.store.canonical_aggregate_key(canonical_fact_key)
            if canonical_fact_key
            else self.store._aggregate_key(
                observation.owner_id, subject, predicate, object_value, observation.content,
            )
        )
        aggregate = self.store.client.hgetall(aggregate_key)
        sources = self.store.client.smembers(self.store._source_key(aggregate_key))
        return EvidenceSummary(
            observation_count=_as_int(aggregate.get("observation_count")),
            distinct_session_count=_as_int(aggregate.get("distinct_session_count")),
            source_count=_as_int(aggregate.get("source_count")),
            source_types=tuple(sorted(str(source) for source in sources)),
            first_seen=aggregate.get("first_seen"),
            last_seen=aggregate.get("last_seen"),
            last_confidence=_as_float(aggregate.get("last_confidence")),
            source_record_key=observation.source_record_key,
        )


class PromotionPolicy:
    """Deterministic, side-effect-free recommendation policy for shadow mode."""

    _SOURCE_QUALITY = {
        "enterprise_excel": 1.0,
        "enterprise_cache_request": 0.95,
        "enterprise_cache_refresh": 0.95,
        "explicit_user_statement": 0.90,
        "profile_explicit": 0.90,
        "conversation_message": 0.80,
        "conversation_extraction": 0.70,
        "profile_note": 0.65,
        "legacy_migration": 0.60,
        "conversation": 0.55,
    }

    def __init__(
        self,
        *,
        minimum_observations: int = 3,
        minimum_distinct_sessions: int = 3,
        threshold: float = 0.70,
        recency_days: int = 90,
    ):
        self.minimum_observations = minimum_observations
        self.minimum_distinct_sessions = minimum_distinct_sessions
        self.threshold = threshold
        self.recency_days = recency_days

    def evaluate(
        self,
        observation: Observation,
        evidence: EvidenceSummary,
        *,
        evaluation_time: datetime | None = None,
    ) -> PromotionDecision:
        """Return the same decision for the same immutable inputs and clock."""
        evaluation_time = evaluation_time or datetime.now(timezone.utc)
        confidence = evidence.last_confidence
        if confidence is None:
            # Reuse the existing deterministic hedge handling.  No external
            # lookup or new confidence computation is introduced in shadow mode.
            confidence = source_certainty(observation.content)
        observation_strength = clamp(evidence.observation_count / self.minimum_observations, 0.0, 1.0)
        session_strength = clamp(evidence.distinct_session_count / self.minimum_distinct_sessions, 0.0, 1.0)
        corroboration = clamp(max(evidence.source_count - 1, 0) / 2, 0.0, 1.0)
        source_quality = max(
            [self._SOURCE_QUALITY.get(source, 0.40) for source in (*evidence.source_types, observation.source)],
            default=0.40,
        )
        provenance_quality = 1.0 if (evidence.source_record_key or observation.message_id or observation.job_id) else 0.5
        recency = self._recency_strength(evidence.last_seen, evaluation_time)
        score = round(clamp(
            0.25 * confidence
            + 0.25 * observation_strength
            + 0.20 * session_strength
            + 0.10 * corroboration
            + 0.10 * source_quality
            + 0.05 * provenance_quality
            + 0.05 * recency,
            0.0,
            1.0,
        ), 6)

        authoritative_enterprise = (
            "enterprise_excel" in evidence.source_types and bool(evidence.source_record_key)
        )
        eligible = authoritative_enterprise or (
            evidence.observation_count >= self.minimum_observations
            and evidence.distinct_session_count >= self.minimum_distinct_sessions
            and score >= self.threshold
        )
        if authoritative_enterprise:
            reason = "authoritative_enterprise_evidence"
        elif eligible:
            reason = "evidence_and_session_thresholds_met"
        else:
            gaps = []
            if evidence.observation_count < self.minimum_observations:
                gaps.append("observation_count")
            if evidence.distinct_session_count < self.minimum_distinct_sessions:
                gaps.append("distinct_sessions")
            if score < self.threshold:
                gaps.append("score")
            reason = "insufficient_" + "_and_".join(gaps or ["evidence"])
        return PromotionDecision(
            eligible=eligible,
            recommended_action="PROMOTE" if eligible else "COLLECT_MORE_EVIDENCE",
            reason=reason,
            evidence_summary=evidence,
            score=score,
            threshold=self.threshold,
            evaluation_timestamp=evaluation_time.isoformat(),
        )

    def _recency_strength(self, last_seen: str | None, evaluation_time: datetime) -> float:
        if not last_seen:
            return 0.0
        try:
            seen = datetime.fromisoformat(last_seen)
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (evaluation_time - seen).total_seconds())
        except ValueError:
            return 0.0
        return clamp(1.0 - age_seconds / (self.recency_days * 86_400), 0.0, 1.0)


promotion_policy = PromotionPolicy(minimum_distinct_sessions=settings.PROFILE_MIN_DISTINCT_SESSIONS)
evidence_snapshot_reader = EvidenceSnapshotReader()


def evaluate_shadow(validated: ValidatedObservation, **details) -> PromotionDecision | None:
    """Read, evaluate, and log a decision without changing the writer path."""
    try:
        observation = validated.observation
        evidence = evidence_snapshot_reader.read(observation, **details)
        decision = promotion_policy.evaluate(observation, evidence)
        logger.info("promotion_shadow_decision %s", json.dumps(decision.to_log_record(), sort_keys=True))
        return decision
    except Exception as error:  # pragma: no cover - depends on external Redis failure
        logger.warning(
            "Promotion shadow evaluation failed; continuing existing writer: observation_id=%s error_type=%s",
            validated.observation.observation_id,
            type(error).__name__,
        )
        return None
