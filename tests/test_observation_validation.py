"""Phase 2.5 regression coverage for the pure Observation validation gate."""

from uuid import uuid4

import pytest

from app.models import Observation
from app.services.observation_validation import (
    DuplicateObservationError,
    ObservationValidationError,
    observation_validator,
)


def test_valid_observation_passes_the_shared_validator():
    observation = Observation.create(
        source="conversation_message",
        content="I prefer concise answers.",
        owner_id="E0001",
        agent_id="agent-a",
        message_id="message-1",
        session_id="session-1",
        metadata={"role": "user"},
    )

    result = observation_validator.validate(observation)

    assert result.observation is observation
    assert len(result.idempotency_key) == 64


def test_malformed_observation_is_rejected_before_a_writer():
    malformed = Observation.create(
        source="untrusted_external_source",
        content="unsupported input",
        owner_id="E0001",
    )

    with pytest.raises(ObservationValidationError, match="unrecognized observation source"):
        observation_validator.validate(malformed)


def test_missing_required_observation_fields_are_rejected():
    missing_record_lineage = Observation.create(
        source="enterprise_cache_request",
        content="18",
        owner_id="E0001",
    )

    with pytest.raises(ObservationValidationError, match="source_record_key is required"):
        observation_validator.validate(missing_record_lineage)


def test_duplicate_idempotency_metadata_is_detected_only_within_a_batch():
    original = Observation.create(
        source="conversation_message",
        content="I prefer concise answers.",
        owner_id="E0001",
        message_id="message-1",
    )
    replay = original.model_copy(update={"observation_id": str(uuid4())})

    # Validation verifies the duplicate input identity but does not create any
    # global state or alter the existing writer's replay behaviour.
    with pytest.raises(DuplicateObservationError, match="Duplicate observation"):
        observation_validator.validate_many([original, replay])
