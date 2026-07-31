"""Phase 2 contract tests: write ingresses emit immutable Observations."""

import pytest
from pydantic import ValidationError

from app.memory_orchestrator import ApplicationOrchestrator
from app.models import ConversationMessage, KnowledgeRecord, Observation
from app.services.enterprise_cache import EnterpriseProfileCacheService
from app.services.knowledge import EnterpriseField
from app.services.profile import ProfileService


def test_observation_contract_is_immutable_and_json_serializable():
    observation = Observation.create(
        source="conversation_message",
        content="I prefer concise answers.",
        owner_id="e0001",
        metadata={"role": "user", "attempt": 1},
    )

    assert observation.owner_id == "E0001"
    assert observation.metadata[0].key == "attempt"
    assert observation.to_job_payload()["content"] == "I prefer concise answers."
    with pytest.raises(ValidationError):
        observation.content = "changed"


def test_extraction_job_carries_one_observation_without_changing_job_shape(monkeypatch):
    message = ConversationMessage(role="user", content="Hello", timestamp="2026-01-01T00:00:00", id="message-1", session_id="session-1")
    evicted = ConversationMessage(role="user", content="I work at Jindal Steel", timestamp="2026-01-01T00:00:01", id="message-2", session_id="session-1")
    queued = []

    monkeypatch.setattr(
        "app.memory_orchestrator.conversation_service.add_message_with_receipt",
        lambda **_kwargs: (message, evicted),
    )
    monkeypatch.setattr("app.memory_orchestrator.conversation_service.get_extraction_window", lambda **_kwargs: [evicted])
    monkeypatch.setattr("app.memory_orchestrator.profile_service.should_enqueue", lambda *_args: False)
    monkeypatch.setattr("app.memory_orchestrator.ApplicationOrchestrator._store_immediate_named_fact", lambda *_args: None)
    monkeypatch.setattr("app.memory_orchestrator.job_queue.enqueue", lambda queue, payload: queued.append((queue, payload)))

    ApplicationOrchestrator().process_message("E0001", "agent-a", "user", "Hello")

    assert queued[0][0] == "memory-extraction"
    observation = Observation.model_validate(queued[0][1]["observation"])
    assert observation.source == "conversation_eviction"
    assert observation.message_id == "message-2"
    assert observation.session_id == "session-1"


def test_profile_and_enterprise_cache_ingresses_carry_observations(monkeypatch):
    profile_observations = []
    profile = ProfileService()
    monkeypatch.setattr("app.services.profile.settings.PROFILE_LEARNING_ENABLED", True)
    monkeypatch.setattr(
        profile,
        "_persist_note",
        lambda _owner, _content, _payload, observation: profile_observations.append(observation),
    )
    profile.process_message({
        "user_id": "E0001", "agent_id": "agent-a", "role": "user",
        "content": "I enjoy photography.", "message_id": "message-1", "session_id": "session-1",
    })

    queued = []
    monkeypatch.setattr("app.services.enterprise_cache.settings.ENTERPRISE_ON_DEMAND_CACHE_ENABLED", True)
    monkeypatch.setattr("app.services.jobs.job_queue.enqueue", lambda queue, payload: queued.append((queue, payload)))
    EnterpriseProfileCacheService().enqueue_records(
        "E0001",
        [KnowledgeRecord(sheet_name="HR", row_data={"EMP_ID": "E0001", "LEAVE_BAL": 18})],
        [EnterpriseField("HR", "LEAVE_BAL", "leave balance")],
    )

    assert profile_observations[0].source == "profile_observation"
    observation = Observation.model_validate(queued[0][1]["observation"])
    assert observation.source == "enterprise_cache_request"
    assert observation.source_record_key == "excel:HR:E0001"
