"""Passive post-write comparison capture for PromotionPolicy calibration."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models import Observation
from app.services.confidence import source_certainty
from app.services.observation_validation import ValidatedObservation
from app.services.promotion_policy import PromotionDecision, promotion_policy

logger = logging.getLogger(__name__)

PROMOTED = "PROMOTED"
NOT_PROMOTED = "NOT_PROMOTED"
WRITER_FAILED = "WRITER_FAILED"


@dataclass(frozen=True)
class PromotionComparisonRecord:
    """Redacted, immutable calibration data captured after a writer outcome."""

    comparison_key: str
    observation_id: str
    parent_observation_id: str | None
    owner_id: str | None
    observation_source: str
    cohort: str
    writer_type: str
    writer_outcome: str
    comparison_result: str
    decision_available: bool
    recommended_action: str | None
    eligible: bool | None
    reason: str | None
    score: float | None
    threshold: float | None
    evaluation_timestamp: str | None
    observation_count: int | None
    distinct_session_count: int | None
    source_count: int | None
    source_types: tuple[str, ...]
    effective_confidence: float | None
    recency_strength: float | None
    source_quality: float | None
    provenance_present: bool
    authoritative_enterprise: bool
    writer_error_type: str | None
    captured_at: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class PromotionComparisonRecorder:
    """Best-effort JSONL recorder; it never changes a writer's control flow."""

    @staticmethod
    def _comparison_key(observation: Observation, writer_type: str) -> str:
        material = f"{observation.observation_id}:{writer_type}:v1"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _result(decision: PromotionDecision | None, writer_outcome: str) -> str:
        if writer_outcome == WRITER_FAILED:
            return WRITER_FAILED
        if decision is None:
            return "DECISION_UNAVAILABLE"
        if (decision.recommended_action == "PROMOTE") == (writer_outcome == PROMOTED):
            return "AGREEMENT"
        return "MISSED_PROMOTION" if writer_outcome == PROMOTED else "FALSE_PROMOTION"

    def build(
        self,
        validated: ValidatedObservation,
        decision: PromotionDecision | None,
        writer_type: str,
        writer_outcome: str,
        error: Exception | None = None,
    ) -> PromotionComparisonRecord:
        observation = validated.observation
        evidence = decision.evidence_summary if decision else None
        source_types = evidence.source_types if evidence else ()
        effective_confidence = (
            evidence.last_confidence if evidence and evidence.last_confidence is not None
            else source_certainty(observation.content) if decision else None
        )
        evaluation_time = datetime.fromisoformat(decision.evaluation_timestamp) if decision else datetime.now(timezone.utc)
        recency = promotion_policy._recency_strength(evidence.last_seen, evaluation_time) if evidence else None
        source_quality = (
            max(
                [promotion_policy._SOURCE_QUALITY.get(source, 0.40) for source in (*source_types, observation.source)],
                default=0.40,
            ) if decision else None
        )
        authoritative_enterprise = bool(
            evidence and "enterprise_excel" in source_types and evidence.source_record_key
        )
        return PromotionComparisonRecord(
            comparison_key=self._comparison_key(observation, writer_type),
            observation_id=observation.observation_id,
            parent_observation_id=observation.parent_observation_id,
            owner_id=observation.owner_id,
            observation_source=observation.source,
            cohort=settings.PROMOTION_COMPARISON_COHORT,
            writer_type=writer_type,
            writer_outcome=writer_outcome,
            comparison_result=self._result(decision, writer_outcome),
            decision_available=decision is not None,
            recommended_action=decision.recommended_action if decision else None,
            eligible=decision.eligible if decision else None,
            reason=decision.reason if decision else None,
            score=decision.score if decision else None,
            threshold=decision.threshold if decision else None,
            evaluation_timestamp=decision.evaluation_timestamp if decision else None,
            observation_count=evidence.observation_count if evidence else None,
            distinct_session_count=evidence.distinct_session_count if evidence else None,
            source_count=evidence.source_count if evidence else None,
            source_types=source_types,
            effective_confidence=effective_confidence,
            recency_strength=recency,
            source_quality=source_quality,
            provenance_present=bool(observation.source_record_key or observation.message_id or observation.job_id),
            authoritative_enterprise=authoritative_enterprise,
            writer_error_type=type(error).__name__ if error else None,
            captured_at=datetime.now(timezone.utc).isoformat(),
        )

    def record(
        self,
        validated: ValidatedObservation,
        decision: PromotionDecision | None,
        writer_type: str,
        writer_outcome: str,
        error: Exception | None = None,
    ) -> PromotionComparisonRecord | None:
        if not settings.PROMOTION_COMPARISON_ENABLED:
            return None
        try:
            record = self.build(validated, decision, writer_type, writer_outcome, error)
            directory = Path(settings.PROMOTION_COMPARISON_DIRECTORY)
            directory.mkdir(parents=True, exist_ok=True)
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            target = directory / f"{settings.PROCESS_ROLE}-promotion-comparison-{date}.jsonl"
            with target.open("a", encoding="utf-8") as handle:
                handle.write(record.to_json() + "\n")
            self._remove_expired_reports(directory)
            logger.info("promotion_comparison %s", record.to_json())
            return record
        except Exception as capture_error:  # pragma: no cover - filesystem errors are environment dependent
            logger.warning(
                "Promotion comparison capture failed; continuing existing writer: observation_id=%s error_type=%s",
                validated.observation.observation_id,
                type(capture_error).__name__,
            )
            return None

    @staticmethod
    def _remove_expired_reports(directory: Path) -> None:
        cutoff = time.time() - settings.PROMOTION_COMPARISON_RETENTION_DAYS * 86_400
        for path in directory.glob("*-promotion-comparison-*.jsonl"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()


promotion_comparison_recorder = PromotionComparisonRecorder()


def record_promotion_comparison(
    validated: ValidatedObservation,
    decision: PromotionDecision | None,
    writer_type: str,
    writer_outcome: str,
    error: Exception | None = None,
) -> PromotionComparisonRecord | None:
    """Compatibility helper that keeps recorder failures outside writer logic."""
    return promotion_comparison_recorder.record(validated, decision, writer_type, writer_outcome, error)
