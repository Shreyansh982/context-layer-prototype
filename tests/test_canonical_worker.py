from app.models import ExtractedFact
from app.worker import handle_extraction


def test_supported_extraction_uses_canonical_upsert_not_sentence_memory(monkeypatch):
    extracted = [ExtractedFact(content="User works at Jindal Steel")]
    monkeypatch.setattr("app.worker.extraction_service.extract", lambda _messages: extracted)
    upserts, legacy = [], []
    monkeypatch.setattr("app.worker.canonical_fact_service.upsert", lambda *args, **kwargs: upserts.append((args, kwargs)))
    monkeypatch.setattr("app.worker.memory_service.store_fact", lambda **kwargs: legacy.append(kwargs))

    handle_extraction({
        "job_id": "job-1", "user_id": "E0001", "agent_id": "agent-a",
        "messages": [{"role": "user", "content": "I work at Jindal Steel", "timestamp": "2026-01-01T00:00:00"}],
    })

    assert len(upserts) == 1
    assert upserts[0][0][0].predicate == "WORKS_AT"
    observation = upserts[0][1]["observation"]
    assert observation.source == "conversation_extraction"
    assert observation.owner_id == "E0001"
    assert observation.parent_observation_id
    assert legacy == []


def test_unsupported_extraction_is_not_persisted_as_legacy_memory(monkeypatch):
    extracted = [ExtractedFact(content="I recently moved to Bangalore.", subject="I")]
    monkeypatch.setattr("app.worker.extraction_service.extract", lambda _messages: extracted)
    legacy, notes = [], []
    monkeypatch.setattr("app.worker.canonical_fact_service.upsert", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not canonical")))
    monkeypatch.setattr("app.worker.memory_service.store_fact", lambda **kwargs: legacy.append(kwargs))
    monkeypatch.setattr(
        "app.worker.profile_service.persist_unsupported_self_fact",
        lambda *args, **kwargs: notes.append((args, kwargs)) or True,
    )

    handle_extraction({
        "user_id": "E0001", "agent_id": "agent-a",
        "messages": [{"role": "user", "content": "I recently moved to Bangalore.", "timestamp": "2026-01-01T00:00:00"}],
    })

    assert legacy == []
    assert len(notes) == 1


def test_general_knowledge_extraction_is_not_redirected_to_profile_note(monkeypatch):
    extracted = [ExtractedFact(content="What is GraphDB?", subject="USER")]
    monkeypatch.setattr("app.worker.extraction_service.extract", lambda _messages: extracted)
    notes = []
    monkeypatch.setattr("app.worker.profile_service._persist_note", lambda *args, **kwargs: notes.append((args, kwargs)))

    handle_extraction({
        "user_id": "E0001", "agent_id": "agent-a",
        "messages": [{"role": "user", "content": "What is GraphDB?", "timestamp": "2026-01-01T00:00:00"}],
    })

    assert notes == []
