"""Phase 3B regression tests for the pure, shadow-only promotion policy."""

from datetime import datetime, timezone

from app.models import MemoryScope, Observation
from app.services.evidence import EvidenceStore
from app.services.memory import MemoryService
from app.services.observation_validation import observation_validator
from app.services.promotion_policy import EvidenceSnapshotReader, EvidenceSummary, PromotionPolicy


class _ReadOnlyRedis:
    def __init__(self, aggregate, sources):
        self.aggregate = aggregate
        self.sources = sources
        self.reads = []

    def hgetall(self, key):
        self.reads.append(("hgetall", key))
        return dict(self.aggregate)

    def smembers(self, key):
        self.reads.append(("smembers", key))
        return set(self.sources)

    def __getattr__(self, name):
        raise AssertionError(f"Promotion evaluation attempted Redis write: {name}")


class _Queue:
    def __init__(self):
        self.entries = []

    def enqueue(self, stream, payload):
        self.entries.append((stream, payload))


def _observation():
    return Observation.create(
        source="conversation_extraction",
        content="I work at Jindal Steel.",
        owner_id="E0001",
        agent_id="agent-a",
        message_id="message-1",
        session_id="session-1",
        job_id="job-1",
        parent_observation_id="00000000-0000-0000-0000-000000000001",
    )


def _summary():
    return EvidenceSummary(
        observation_count=3,
        distinct_session_count=3,
        source_count=2,
        source_types=("conversation_extraction", "explicit_user_statement"),
        first_seen="2026-01-01T00:00:00+00:00",
        last_seen="2026-01-10T00:00:00+00:00",
        last_confidence=0.8,
    )


def test_promotion_policy_is_deterministic_for_equal_evidence():
    observation = _observation()
    policy = PromotionPolicy()
    at = datetime(2026, 1, 10, tzinfo=timezone.utc)

    first = policy.evaluate(observation, _summary(), evaluation_time=at)
    second = policy.evaluate(observation, _summary(), evaluation_time=at)

    assert first == second
    assert first.eligible is True
    assert first.recommended_action == "PROMOTE"
    assert first.reason == "evidence_and_session_thresholds_met"


def test_evidence_snapshot_read_and_evaluation_never_modify_evidence():
    observation = _observation()
    client = _ReadOnlyRedis(
        {
            "observation_count": "2",
            "distinct_session_count": "2",
            "source_count": "1",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-10T00:00:00+00:00",
            "last_confidence": "0.7",
        },
        {"conversation_extraction"},
    )
    reader = EvidenceSnapshotReader(EvidenceStore(client=client))

    summary = reader.read(observation, subject="User", predicate="WORKS_AT", object_value="Jindal Steel Ltd")
    decision = PromotionPolicy().evaluate(
        observation,
        summary,
        evaluation_time=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )

    assert summary.observation_count == 2
    assert decision.eligible is False
    assert [name for name, _ in client.reads] == ["hgetall", "smembers"]


def test_existing_legacy_writer_path_is_unchanged_in_shadow_mode(monkeypatch):
    persisted, policy_calls = [], []
    service = MemoryService(queue=_Queue())
    monkeypatch.setattr(service, "_store_user_memory", lambda memory, stable_hash: persisted.append((memory, stable_hash)))
    monkeypatch.setattr("app.services.memory.record_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.memory.evaluate_shadow", lambda validated, **details: policy_calls.append((validated, details)))

    memory = service.store_fact(
        "User works at Jindal Steel.", "E0001", MemoryScope.USER,
        confidence=0.9, agent_id="agent-a",
    )

    assert memory.content == "User works at Jindal Steel."
    assert len(persisted) == 1
    assert len(policy_calls) == 1
    assert policy_calls[0][0].observation.source == "conversation"


def test_repeated_shadow_evaluation_has_no_mutable_observation_or_evidence_state():
    observation = _observation()
    validated = observation_validator.validate(observation)
    evidence = _summary()
    policy = PromotionPolicy()
    at = datetime(2026, 1, 10, tzinfo=timezone.utc)

    first = policy.evaluate(validated.observation, evidence, evaluation_time=at)
    second = policy.evaluate(validated.observation, evidence, evaluation_time=at)

    assert first == second
    assert validated.observation == observation
    assert evidence == _summary()
