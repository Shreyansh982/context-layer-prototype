"""Regression coverage for passive promotion comparison capture."""

import json
from datetime import datetime, timezone

import pytest

from app.models import MemoryScope, Observation
from app.services.memory import MemoryService
from app.services.observation_validation import observation_validator
from app.services.promotion_comparison import PROMOTED, WRITER_FAILED, PromotionComparisonRecorder
from app.services.promotion_policy import EvidenceSummary, PromotionDecision
from scripts.promotion_calibration_report import load_records, report


class _Queue:
    def __init__(self):
        self.entries = []

    def enqueue(self, stream, payload):
        self.entries.append((stream, payload))


def _validated():
    return observation_validator.validate(Observation.create(
        source="conversation",
        content="Private source text must never reach comparison output.",
        owner_id="E0001",
        agent_id="agent-a",
    ))


def _decision():
    return PromotionDecision(
        eligible=True,
        recommended_action="PROMOTE",
        reason="evidence_and_session_thresholds_met",
        evidence_summary=EvidenceSummary(
            observation_count=3,
            distinct_session_count=3,
            source_count=2,
            source_types=("conversation", "conversation_extraction"),
            first_seen="2026-01-01T00:00:00+00:00",
            last_seen="2026-01-02T00:00:00+00:00",
            last_confidence=0.8,
        ),
        score=0.8,
        threshold=0.7,
        evaluation_timestamp="2026-01-02T00:00:00+00:00",
    )


def test_comparison_recorder_writes_redacted_record(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.promotion_comparison.settings.PROMOTION_COMPARISON_ENABLED", True)
    monkeypatch.setattr("app.services.promotion_comparison.settings.PROMOTION_COMPARISON_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("app.services.promotion_comparison.settings.PROCESS_ROLE", "worker")
    record = PromotionComparisonRecorder().record(_validated(), _decision(), "legacy_memory", PROMOTED)

    saved = json.loads(next(tmp_path.glob("worker-promotion-comparison-*.jsonl")).read_text(encoding="utf-8"))
    assert record.writer_outcome == PROMOTED
    assert saved["comparison_result"] == "AGREEMENT"
    assert "content" not in saved
    assert "Private source text" not in json.dumps(saved)
    assert saved["effective_confidence"] == 0.8


def test_legacy_writer_records_outcome_after_unchanged_durable_write(monkeypatch):
    events, captured = [], []
    service = MemoryService(queue=_Queue())
    monkeypatch.setattr(service, "_store_user_memory", lambda memory, stable_hash: events.append("write"))
    monkeypatch.setattr("app.services.memory.record_evidence", lambda *_args, **_kwargs: events.append("evidence"))
    monkeypatch.setattr("app.services.memory.evaluate_shadow", lambda *_args, **_kwargs: _decision())
    monkeypatch.setattr(
        "app.services.memory.record_promotion_comparison",
        lambda *args, **kwargs: captured.append(args) or events.append("comparison"),
    )

    memory = service.store_fact("User works at Jindal Steel.", "E0001", MemoryScope.USER, agent_id="agent-a")

    assert memory.content == "User works at Jindal Steel."
    assert events == ["evidence", "write", "comparison"]
    assert captured[0][3] == PROMOTED


def test_writer_failure_is_captured_and_original_error_is_preserved(monkeypatch):
    captured = []
    service = MemoryService(queue=_Queue())
    monkeypatch.setattr(service, "_store_user_memory", lambda *_args: (_ for _ in ()).throw(RuntimeError("neo down")))
    monkeypatch.setattr("app.services.memory.record_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.memory.evaluate_shadow", lambda *_args, **_kwargs: _decision())
    monkeypatch.setattr("app.services.memory.record_promotion_comparison", lambda *args, **kwargs: captured.append(args))

    with pytest.raises(RuntimeError, match="neo down"):
        service.store_fact("User works at Jindal Steel.", "E0001", MemoryScope.USER, agent_id="agent-a")

    assert captured[0][3] == WRITER_FAILED
    assert isinstance(captured[0][4], RuntimeError)


def test_calibration_report_deduplicates_retries_and_calculates_metrics():
    base = PromotionComparisonRecorder().build(_validated(), _decision(), "legacy_memory", PROMOTED)
    duplicate = json.loads(base.to_json())
    duplicate["captured_at"] = datetime.now(timezone.utc).isoformat()
    missed = json.loads(base.to_json())
    missed["comparison_key"] = "different"
    missed["recommended_action"] = "COLLECT_MORE_EVIDENCE"
    missed["writer_outcome"] = PROMOTED
    missed["comparison_result"] = "MISSED_PROMOTION"

    result = report([json.loads(base.to_json()), duplicate, missed])

    assert result["total_logical_observations"] == 2
    assert result["current_shadow_policy"]["agreement_rate"] == 0.5
    assert result["current_shadow_policy"]["missed_promotions"] == 1


def test_calibration_report_filters_to_requested_cohort(tmp_path):
    live = json.loads(PromotionComparisonRecorder().build(_validated(), _decision(), "legacy_memory", PROMOTED).to_json())
    live["cohort"] = "live"
    replay = dict(live, comparison_key="replay-key", cohort="replay")
    (tmp_path / "worker-promotion-comparison-2026-01-01.jsonl").write_text(
        json.dumps(live) + "\n" + json.dumps(replay) + "\n", encoding="utf-8",
    )

    assert len(load_records(tmp_path, "live")) == 1
    assert len(load_records(tmp_path, "replay")) == 1
