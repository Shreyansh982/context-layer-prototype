"""Phase 3A regression tests for passive validated-Observation evidence."""

import json

from app.models import MemoryScope, Observation
from app.services.evidence import EvidenceStore, record_evidence
from app.services.memory import MemoryService
from app.services.observation_validation import observation_validator
from app.services.promotion_policy import EvidenceSnapshotReader
from app.worker import handle_extraction
from app.models import ExtractedFact


class _Redis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.sets = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def hincrby(self, key, field, amount):
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = int(bucket.get(field, 0)) + amount
        return bucket[field]

    def hsetnx(self, key, field, value):
        bucket = self.hashes.setdefault(key, {})
        if field in bucket:
            return False
        bucket[field] = value
        return True

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    def scard(self, key):
        return len(self.sets.get(key, set()))

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def expire(self, *_args):
        return True


class _Queue:
    def __init__(self):
        self.entries = []

    def enqueue(self, stream, payload):
        self.entries.append((stream, payload))


def _validated_observation(session_id="session-1"):
    return observation_validator.validate(Observation.create(
        source="conversation_message",
        content="I work at Jindal Steel.",
        owner_id="E0001",
        agent_id="agent-a",
        message_id="message-1",
        session_id=session_id,
    ))


def test_evidence_store_records_expected_shadow_fields():
    client = _Redis()
    store = EvidenceStore(client=client)
    validated = _validated_observation()

    result = store.record(
        validated,
        subject="User",
        predicate="WORKS_AT",
        object_value="Jindal Steel Ltd",
        confidence=0.8,
        provenance={"writer": "test"},
    )

    assert result.recorded is True
    event = json.loads(client.values[result.evidence_key])
    assert event["observation_id"] == validated.observation.observation_id
    assert event["owner_id"] == "E0001"
    assert event["predicate"] == "WORKS_AT"
    assert event["confidence"] == 0.8
    aggregate = client.hashes[result.aggregate_key]
    assert aggregate["observation_count"] == 1
    assert aggregate["distinct_session_count"] == "1"
    assert aggregate["first_seen"] == aggregate["last_seen"]


def test_duplicate_delivery_is_idempotent_but_new_session_is_counted():
    client = _Redis()
    store = EvidenceStore(client=client)
    first = _validated_observation("session-1")
    replay = first
    second = observation_validator.validate(Observation.create(
        source="conversation_message",
        content="I work at Jindal Steel.",
        owner_id="E0001",
        agent_id="agent-a",
        message_id="message-2",
        session_id="session-2",
    ))

    first_result = store.record(first, subject="User", predicate="WORKS_AT", object_value="Jindal Steel Ltd")
    assert store.record(replay, subject="User", predicate="WORKS_AT", object_value="Jindal Steel Ltd").recorded is False
    store.record(second, subject="User", predicate="WORKS_AT", object_value="Jindal Steel Ltd")

    aggregate = client.hashes[first_result.aggregate_key]
    assert aggregate["observation_count"] == 2
    assert aggregate["distinct_session_count"] == "2"


def test_canonical_aggregate_receives_raw_observation_once_and_policy_reads_it():
    client = _Redis()
    store = EvidenceStore(client=client)
    validated = _validated_observation("session-1")

    # The extractor records its raw representation first.  Normalization then
    # resolves the same observation to a different canonical representation.
    raw = store.record(
        validated,
        subject="I",
        predicate="works_for",
        object_value="Jindal Steel",
    )
    canonical = store.record(
        validated,
        subject="E0001",
        predicate="WORKS_AT",
        object_value="Jindal Steel Ltd",
        canonical_fact_key="fact:works-at:e0001:jindal-steel",
    )

    assert raw.recorded is True
    assert canonical.recorded is True
    assert len(client.values) == 2  # immutable event + canonical receipt
    aggregate = client.hashes[canonical.aggregate_key]
    assert aggregate["aggregate_kind"] == "canonical_fact"
    assert aggregate["observation_count"] == 1
    assert aggregate["distinct_session_count"] == "1"

    summary = EvidenceSnapshotReader(store).read(
        validated.observation,
        canonical_fact_key="fact:works-at:e0001:jindal-steel",
    )
    assert summary.observation_count == 1
    assert summary.distinct_session_count == 1
    assert summary.source_types == ("conversation_message",)

    # A retry can neither create a second event nor inflate the canonical
    # aggregate, even though raw evidence was recorded first.
    assert store.record(
        validated,
        subject="E0001",
        predicate="WORKS_AT",
        object_value="Jindal Steel Ltd",
        canonical_fact_key="fact:works-at:e0001:jindal-steel",
    ).recorded is False
    assert client.hashes[canonical.aggregate_key]["observation_count"] == 1


def test_one_observation_can_support_multiple_atomic_canonical_facts_once_each():
    client = _Redis()
    store = EvidenceStore(client=client)
    validated = _validated_observation("session-1")

    first = store.record(validated, canonical_fact_key="fact:member-of", predicate="MEMBER_OF")
    second = store.record(validated, canonical_fact_key="fact:works-at", predicate="WORKS_AT")

    assert first.recorded is True
    assert second.recorded is True
    assert client.hashes[first.aggregate_key]["observation_count"] == 1
    assert client.hashes[second.aggregate_key]["observation_count"] == 1


def test_legacy_writer_records_evidence_before_unchanged_persistence(monkeypatch):
    persisted, events = [], []
    service = MemoryService(queue=_Queue())
    monkeypatch.setattr(service, "_store_user_memory", lambda memory, stable_hash: persisted.append((memory, stable_hash)))
    monkeypatch.setattr("app.services.memory.record_evidence", lambda validated, **details: events.append((validated, details)))

    memory = service.store_fact(
        "User works at Jindal Steel.", "E0001", MemoryScope.USER,
        confidence=0.9, agent_id="agent-a",
    )

    assert memory.content == "User works at Jindal Steel."
    assert len(persisted) == 1
    assert len(events) == 1
    assert events[0][1]["confidence"] == 0.9


def test_extraction_observations_record_evidence_without_changing_existing_writer(monkeypatch):
    events, writes = [], []
    monkeypatch.setattr("app.worker.extraction_service.extract", lambda _messages: [ExtractedFact(content="User works at Jindal Steel")])
    monkeypatch.setattr("app.worker.record_evidence", lambda validated, **details: events.append((validated, details)))
    monkeypatch.setattr("app.worker.canonical_fact_service.upsert", lambda *args, **kwargs: writes.append((args, kwargs)) or "fact-1")

    handle_extraction({
        "job_id": "job-1", "user_id": "E0001", "agent_id": "agent-a",
        "messages": [{"role": "user", "content": "I work at Jindal Steel", "timestamp": "2026-01-01T00:00:00"}],
    })

    assert [event[0].observation.source for event in events] == ["memory_extraction_job", "conversation_extraction"]
    assert writes[0][0][0].predicate == "WORKS_AT"


def test_evidence_failure_does_not_change_existing_writer_path(monkeypatch):
    validated = _validated_observation()
    monkeypatch.setattr("app.services.evidence.evidence_store.record", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("redis unavailable")))

    assert record_evidence(validated) is None
