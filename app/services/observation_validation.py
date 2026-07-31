"""Pure validation for Phase 2 Observation inputs.

This module has no database, queue, cache, or promotion dependency.  It only
accepts structurally sound, attributable observations before existing writers
run.  Duplicate detection is intentionally batch-local: it verifies
idempotency metadata without changing the current retry/deduplication policy.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from app.models import Observation


class ObservationValidationError(ValueError):
    """An observation cannot safely enter an existing writer."""


class DuplicateObservationError(ObservationValidationError):
    """Two observations in one validation batch have the same input identity."""


@dataclass(frozen=True)
class ValidatedObservation:
    """Validation output; no persistence or policy decision is attached."""

    observation: Observation
    idempotency_key: str


class ObservationValidator:
    """Shared, deterministic gate for all current memory write ingresses."""

    _KNOWN_SOURCES = frozenset({
        "conversation",
        "conversation_message",
        "conversation_eviction",
        "memory_extraction_job",
        "conversation_extraction",
        "explicit_user_statement",
        "profile_observation",
        "profile_explicit",
        "profile_inferred",
        "profile_note",
        "unsupported_self_fact",
        "enterprise_cache_request",
        "enterprise_cache_refresh",
        "enterprise_cache_close",
        "enterprise_snapshot",
        "enterprise_excel",
        "company_policy_ingestion",
        "legacy_migration",
        "knowledge_memory_refresh",
    })

    _OWNER_REQUIRED = frozenset({
        "conversation_message",
        "conversation_eviction",
        "memory_extraction_job",
        "conversation_extraction",
        "explicit_user_statement",
        "profile_observation",
        "profile_explicit",
        "profile_inferred",
        "profile_note",
        "unsupported_self_fact",
        "enterprise_cache_request",
        "enterprise_cache_refresh",
        "enterprise_excel",
        "legacy_migration",
        "knowledge_memory_refresh",
    })

    _PARENT_REQUIRED = frozenset({"conversation_extraction", "enterprise_excel"})
    _SOURCE_RECORD_REQUIRED = frozenset({
        "enterprise_cache_request",
        "enterprise_cache_refresh",
        "enterprise_excel",
        "legacy_migration",
    })

    def validate(self, value: Observation | Mapping[str, object]) -> ValidatedObservation:
        """Validate one observation without reading or changing any state."""
        try:
            observation = value if isinstance(value, Observation) else Observation.model_validate(value)
        except ValidationError as error:
            raise ObservationValidationError(f"Malformed observation: {error}") from error

        errors: list[str] = []
        if not observation.observation_id.strip():
            errors.append("observation_id is required")
        else:
            try:
                UUID(observation.observation_id)
            except ValueError:
                errors.append("observation_id must be a UUID")
        if observation.source not in self._KNOWN_SOURCES:
            errors.append(f"unrecognized observation source: {observation.source!r}")
        if not observation.content.strip():
            errors.append("content is required")
        if observation.source in self._OWNER_REQUIRED and not observation.owner_id:
            errors.append(f"owner_id is required for source {observation.source!r}")
        if observation.owner_id and observation.owner_id != observation.owner_id.upper():
            errors.append("owner_id must be normalized to uppercase")
        if observation.source in self._PARENT_REQUIRED and not observation.parent_observation_id:
            errors.append(f"parent_observation_id is required for source {observation.source!r}")
        if observation.parent_observation_id:
            try:
                UUID(observation.parent_observation_id)
            except ValueError:
                errors.append("parent_observation_id must be a UUID")
        if observation.source in self._SOURCE_RECORD_REQUIRED and not observation.source_record_key:
            errors.append(f"source_record_key is required for source {observation.source!r}")

        metadata_keys = [item.key for item in observation.metadata]
        if any(not key.strip() for key in metadata_keys):
            errors.append("metadata keys must be non-empty")
        if len(metadata_keys) != len(set(metadata_keys)):
            errors.append("metadata keys must be unique")

        if errors:
            raise ObservationValidationError("; ".join(errors))
        return ValidatedObservation(observation=observation, idempotency_key=self.idempotency_key(observation))

    def validate_many(self, values: Sequence[Observation | Mapping[str, object]]) -> list[ValidatedObservation]:
        """Validate a finite batch and report duplicate idempotency metadata.

        This intentionally has no cross-request memory.  Existing writers keep
        their current idempotent persistence behaviour; callers can use this
        method in tests or batch ingestion to detect duplicate inputs early.
        """
        validated = [self.validate(value) for value in values]
        seen: set[str] = set()
        for result in validated:
            if result.idempotency_key in seen:
                raise DuplicateObservationError(
                    f"Duplicate observation idempotency key: {result.idempotency_key}"
                )
            seen.add(result.idempotency_key)
        return validated

    @staticmethod
    def idempotency_key(observation: Observation) -> str:
        """Derive stable replay metadata without storing or enforcing it."""
        material = {
            "source": observation.source,
            "content": observation.content.strip(),
            "owner_id": observation.owner_id,
            "agent_id": observation.agent_id,
            "message_id": observation.message_id,
            "session_id": observation.session_id,
            "job_id": observation.job_id,
            "source_record_key": observation.source_record_key,
            "parent_observation_id": observation.parent_observation_id,
            "metadata": [(item.key, item.value) for item in observation.metadata],
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


observation_validator = ObservationValidator()
