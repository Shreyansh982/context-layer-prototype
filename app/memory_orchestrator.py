"""
Memory Orchestrator

Coordinates the different services of the Context Layer.
"""

from app.services.conversation import conversation_service
from app.services.confidence import score
from app.services.extraction import extraction_service
from app.services.memory import memory_service
from app.services.facts import canonical_fact_service, fact_normalizer
from app.models import ContextWriteIntent, ExtractedFact, MemoryScope, Observation
from app.services.jobs import job_queue
from app.services.profile import profile_service
from app.config import settings
from app.performance import current_operation_id
from app.extraction_trace import new_trace_id
from app.services.observation_validation import observation_validator
from app.services.evidence import record_evidence
import logging
import re
from uuid import uuid4

logger = logging.getLogger(__name__)

class ApplicationOrchestrator:
    def process_message(
        self,
        user_id: str,
        agent_id: str,
        role: str,
        content: str
    ) -> None:
        message, evicted_message = conversation_service.add_message_with_receipt(
            user_id=user_id,
            role=role,
            content=content
        )

        # Profile learning is deterministic and durable, but never belongs on
        # the answer path.  Only plausible profile statements enter the queue.
        if profile_service.should_enqueue(role, content):
            observation = Observation.create(
                source="conversation_message",
                content=content,
                owner_id=user_id,
                agent_id=agent_id,
                message_id=message.id,
                session_id=message.session_id,
                metadata={"role": role},
            )
            validated_observation = observation_validator.validate(observation)
            observation = validated_observation.observation
            record_evidence(validated_observation, provenance={"ingress": "profile_queue", "role": role})
            job_queue.enqueue("profile-observation", {
                "user_id": user_id, "agent_id": agent_id, "role": role,
                "content": content, "message_id": message.id,
                "session_id": message.session_id,
                "observation": observation.to_job_payload(),
            })

        self._store_immediate_named_fact(user_id, agent_id, role, content, message)

        # Only user statements can become user memories.  Extracting facts
        # from assistant messages feeds model-generated claims back into the
        # durable store.
        if evicted_message is None or evicted_message.role != "user":
            return

        logger.info(
            "Eviction fired for user_id=%s, evicted message: %r",
            user_id, evicted_message.content[:80],
        )

        # Build a small context window around the
        # evicted message for better extraction.
        extraction_window = conversation_service.get_extraction_window(
            user_id=user_id,
            evicted_message=evicted_message
        )

        job_id = str(uuid4())
        extraction_observation = Observation.create(
            source="conversation_eviction",
            content=evicted_message.content,
            owner_id=user_id,
            agent_id=agent_id,
            message_id=evicted_message.id,
            session_id=evicted_message.session_id,
            job_id=job_id,
            metadata={"role": evicted_message.role},
        )
        validated_extraction_observation = observation_validator.validate(extraction_observation)
        extraction_observation = validated_extraction_observation.observation
        record_evidence(validated_extraction_observation, provenance={"ingress": "memory_extraction_queue"})
        payload = {
            "job_id": job_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "messages": [message.model_dump(mode="json") for message in extraction_window],
            "observation": extraction_observation.to_job_payload(),
        }
        if settings.EXTRACTION_TRACE:
            # The request ID is already present in routing logs. Reuse it for
            # this deferred job so the API and worker records correlate.
            payload["trace_id"] = current_operation_id() or new_trace_id()
            payload["trace_original_message"] = evicted_message.content
            payload["trace_message_id"] = evicted_message.id
        job_queue.enqueue(
            "memory-extraction",
            payload,
        )

    def get_conversation(
        self,
        user_id: str,
    ):
        return conversation_service.get_conversation(
            user_id
        )

    def get_memory(
        self,
        user_id: str,
        agent_id: str,
    ):
        return memory_service.get_user_memory(
            user_id, agent_id
        )

    def dispatch_context_write_intents(self, intents: list[ContextWriteIntent]) -> int:
        """Queue distinct post-read mutations without making retrieval stateful.

        Intent execution belongs to the worker.  This API-side boundary only
        records durable Redis Stream work; the worker remains responsible for
        invoking the existing validated/evidenced writers.
        """
        dispatched = 0
        seen: set[str] = set()
        for intent in intents:
            key = intent.idempotency_key()
            if key in seen:
                continue
            seen.add(key)
            message_id = job_queue.enqueue_once(
                "context-write-intent",
                intent.model_dump(mode="json"),
                idempotency_key=f"context-layer:context-write-intent:{key}",
                ttl_seconds=settings.ENTERPRISE_CACHE_REFRESH_INTERVAL_SECONDS,
            )
            if message_id is not None:
                dispatched += 1
        logger.info("Deferred context write intents queued: count=%d", dispatched)
        return dispatched

    def _store_immediate_named_fact(self, user_id: str, agent_id: str, role: str, content: str, message) -> None:
        """Persist explicit self-identification without waiting for buffer eviction."""
        if role != "user":
            return
        match = re.fullmatch(r"\s*my name is\s+(.+?)\s*[.!]?\s*", content, re.I)
        if match:
            logger.info(
                "Immediate name match for user_id=%s: %r", user_id, match.group(1)
            )
            content = f"User's name is {match.group(1)}"
            observation = Observation.create(
                source="explicit_user_statement",
                content=content,
                owner_id=user_id,
                agent_id=agent_id,
                message_id=message.id,
                session_id=message.session_id,
                metadata={"kind": "immediate_name"},
            )
            validated_observation = observation_validator.validate(observation)
            observation = validated_observation.observation
            record_evidence(
                validated_observation,
                subject=user_id.upper(),
                predicate="HAS_NAME",
                object_value=match.group(1),
                provenance={"ingress": "immediate_name"},
            )
            candidates = fact_normalizer.normalize(ExtractedFact(content=content), user_id)
            if settings.CANONICAL_FACTS_ENABLED and candidates:
                canonical_fact_service.upsert(
                    candidates[0], source="explicit_user_statement",
                    provenance={"agent_id": agent_id, "job_id": f"immediate-name:{user_id}"},
                    observation=observation,
                )
            else:
                memory_service.store_fact(
                    content=content, owner_id=user_id, scope=MemoryScope.USER,
                    source="explicit_user_statement", agent_id=agent_id,
                    observation=observation,
                )

application_orchestrator = ApplicationOrchestrator()
