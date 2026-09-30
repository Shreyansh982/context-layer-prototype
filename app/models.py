"""
Contains all common models used throughout the Context Layer.

These models act as contracts between different modules.
Instead of passing dictionaries around, every service
will exchange these objects.
"""

from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic import field_validator
import re

# ENUMS

class MemoryScope(str, Enum):
    """
    Defines who can access a memory.
    """
    COMPANY = "COMPANY"     # UNIVERSAL
    USER = "USER"       # USER-specific / PERSONA
    # AGENT is dynamic operational state only; hard safety rules and tool
    # capabilities belong in PromptBuilder's unconditional fixed block.
    AGENT = "AGENT"

# CONVERSATION

class ConversationMessage(BaseModel):
    """
    Represents a single message in the conversation.
    """
    role: str
    content: str
    timestamp: datetime
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None

# MEMORY

class MemoryFact(BaseModel):
    """
    Represents a fact stored inside memory.
    """
    id: str
    content: str
    scope: MemoryScope
    owner_id: str | None
    agent_id: str | None
    confidence: float
    timestamp: datetime
    source: str = "conversation"
    knowledge_reference: str | None = None


class ObservationMetadata(BaseModel):
    """One immutable, serializable piece of observation provenance."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: str


class Observation(BaseModel):
    """The common immutable input contract for all memory-producing writes.

    This contract describes what was observed, not what should be promoted or
    persisted.  Phase 2 only standardizes these inputs; existing promotion,
    graph persistence, and vector-projection decisions deliberately remain in
    their current services until the lifecycle coordinator is introduced.
    """

    model_config = ConfigDict(frozen=True)

    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    content: str
    occurred_at: datetime = Field(default_factory=datetime.now)
    owner_id: str | None = None
    agent_id: str | None = None
    message_id: str | None = None
    session_id: str | None = None
    job_id: str | None = None
    source_record_key: str | None = None
    parent_observation_id: str | None = None
    metadata: tuple[ObservationMetadata, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        source: str,
        content: str,
        owner_id: str | None = None,
        agent_id: str | None = None,
        message_id: str | None = None,
        session_id: str | None = None,
        job_id: str | None = None,
        source_record_key: str | None = None,
        parent_observation_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "Observation":
        """Build a normalized immutable observation from an existing ingress."""
        return cls(
            source=source,
            content=content,
            owner_id=owner_id.upper() if owner_id else None,
            agent_id=agent_id,
            message_id=message_id,
            session_id=session_id,
            job_id=job_id,
            source_record_key=source_record_key,
            parent_observation_id=parent_observation_id,
            metadata=tuple(
                ObservationMetadata(key=str(key), value=str(value))
                for key, value in sorted((metadata or {}).items())
            ),
        )

    def to_job_payload(self) -> dict[str, Any]:
        """Return the contract in the existing JSON queue-payload format."""
        return self.model_dump(mode="json")

# KNOWLEDGE

class KnowledgeRecord(BaseModel):
    """
    Represents one row retrieved from Excel.
    """
    sheet_name: str
    row_data: dict[str, Any]


class ContextWriteIntent(BaseModel):
    """A deferred mutation discovered while assembling read-only context.

    Context retrieval may determine that a cache entry is stale or that an
    authorized enterprise result would be useful to cache.  It records that
    observation here, but it must not execute the write.  This stays internal
    to the Context Layer and is excluded from public response serialization.
    """

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def idempotency_key(self) -> str:
        """Return the stable queue identity for this deferred operation.

        Context assembly can discover the same work repeatedly.  The key is
        derived only from the immutable intent contract so dispatchers can
        coalesce that work without making the read path stateful.
        """
        encoded = json.dumps(
            self.model_dump(mode="json"),
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

# EXTRACTION

class ExtractedFact(BaseModel):
    """
    Represents a fact extracted from a conversation
    before it is written into long-term memory.
    """

    # ``content`` is retained as evidence and as a compatibility fallback for
    # older extraction jobs.  New jobs populate the structured fields below.
    content: str = ""
    subject: str = "USER"
    subject_type: str = "PERSON"
    predicate: str | None = None
    object: str | None = None
    object_type: str | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)


class FactCandidate(BaseModel):
    """A normalized assertion before it is made durable in the fact graph."""

    subject_key: str
    subject_type: str
    subject_name: str
    predicate: str
    object_key: str | None = None
    object_type: str | None = None
    object_name: str | None = None
    value: str | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    owner_id: str | None = None
    scope: MemoryScope = MemoryScope.USER
    display_text: str
    evidence: str = ""

# CONTEXT

class ContextPackage(BaseModel):
    """
    Contains all context retrieved for answering
    a user query.
    """

    conversation: list[ConversationMessage] = Field(default_factory=list)

    memories: list[MemoryFact] = Field(default_factory=list)

    knowledge: list[KnowledgeRecord] = Field(default_factory=list)

    # A compact, deterministic subset of memory that guides response style.
    # It is additive so existing callers of ContextPackage remain compatible.
    profile: list[MemoryFact] = Field(default_factory=list)
    profile_version: int = 0

    # Read-path side effects are represented, never executed, while context is
    # being assembled.  It is deliberately excluded so QueryResponse and
    # existing prompt/API contracts do not change.
    write_intents: list[ContextWriteIntent] = Field(default_factory=list, exclude=True)

# API

class QueryRequest(BaseModel):
    """
    Request received from the AI Agent.
    """
    user_id: str = Field(min_length=1, max_length=16)
    agent_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=8_000)

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        """Accept the employee-ID format used by the enterprise workbook."""
        normalized = value.strip().upper()
        if not re.fullmatch(r"E\d{4}", normalized):
            raise ValueError("User ID must use the employee format E0001")
        return normalized


class QueryResponse(BaseModel):
    """
    Final response returned back to the AI Agent.
    """
    answer: str
    context_used: list[str]
