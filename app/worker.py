"""Dedicated worker process for non-interactive memory work."""

import logging
import socket
import threading
import time

from app.models import ContextWriteIntent, ConversationMessage, KnowledgeRecord, Observation
from app.services.extraction import extraction_service
from app.services.jobs import job_queue
from app.services.memory import memory_service
from app.services.facts import canonical_fact_service, fact_normalizer
from app.services.enterprise_sync import enterprise_sync_service
from app.config import settings
from app.bootstrap import run_migrations
from app.services.confidence import score
from app.services.profile import profile_service
from app.services.enterprise_cache import enterprise_profile_cache_service
from app.services.knowledge import EnterpriseField
from app.performance import finish_timing, start_timing, timed
from app.extraction_trace import activate as activate_trace, emit as trace_emit, stage as trace_stage, warning as trace_warning
from app.services.predicate_registry import predicate_registry
from app.storage.neo4j import neo4j
from app.services.observation_validation import observation_validator
from app.services.evidence import record_evidence

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def handle_extraction(payload: dict) -> None:
    messages = [ConversationMessage.model_validate(message) for message in payload["messages"]]
    source_observation = (
        Observation.model_validate(payload["observation"])
        if payload.get("observation")
        else Observation.create(
            source="memory_extraction_job",
            content=payload.get("trace_original_message") or (messages[0].content if messages else ""),
            owner_id=payload["user_id"],
            agent_id=payload.get("agent_id"),
            message_id=payload.get("trace_message_id"),
            session_id=messages[-1].session_id if messages else None,
            job_id=payload.get("job_id"),
        )
    )
    validated_source_observation = observation_validator.validate(source_observation)
    source_observation = validated_source_observation.observation
    record_evidence(validated_source_observation, provenance={"ingress": "memory_extraction_worker"})
    with activate_trace(payload.get("trace_id")):
        trace_emit("pipeline_start", original_user_message=payload.get("trace_original_message"),
                   source_message_id=payload.get("trace_message_id"), owner_id=payload["user_id"],
                   message_count=len(messages))
        with trace_stage("extraction"):
            extracted_facts = extraction_service.extract(messages)
        if not extracted_facts:
            trace_emit("memory_worthiness", decision="IGNORE", reason="extractor_returned_no_facts")
        for fact in extracted_facts:
            observation = Observation.create(
                source="conversation_extraction",
                content=fact.content,
                owner_id=payload["user_id"],
                agent_id=payload.get("agent_id"),
                message_id=source_observation.message_id,
                session_id=source_observation.session_id,
                job_id=payload.get("job_id"),
                parent_observation_id=source_observation.observation_id,
                metadata={"extracted_predicate": fact.predicate or ""},
            )
            validated_observation = observation_validator.validate(observation)
            observation = validated_observation.observation
            record_evidence(
                validated_observation,
                subject=fact.subject,
                predicate=fact.predicate,
                object_value=fact.object,
                provenance={"ingress": "extraction", "qualifiers": fact.qualifiers},
            )
            trace_emit("extracted_fact", content=fact.content,
                       entities=[{"value": fact.subject, "type": fact.subject_type}, {"value": fact.object, "type": fact.object_type}],
                       subject=fact.subject, predicate=fact.predicate, object=fact.object, qualifiers=fact.qualifiers)
            with trace_stage("canonicalization"):
                candidates = fact_normalizer.normalize(fact, payload["user_id"])
            if settings.CANONICAL_FACTS_ENABLED and candidates:
                trace_emit("memory_worthiness", decision="STORE", reason="supported_canonical_predicate",
                           predicate=fact.predicate, candidate_count=len(candidates))
                for candidate in candidates:
                    trace_emit("canonical_fact", subject_key=candidate.subject_key, subject_type=candidate.subject_type,
                               predicate=candidate.predicate, object_key=candidate.object_key, value=candidate.value,
                               owner_id=candidate.owner_id, qualifiers=candidate.qualifiers, display_text=candidate.display_text)
                    with trace_stage("graph_write", predicate=candidate.predicate):
                        fact_key = canonical_fact_service.upsert(
                            candidate, source="conversation_extraction",
                            provenance={
                                "job_id": payload.get("job_id"), "source_message_count": len(messages),
                                "agent_id": payload["agent_id"], "trace_id": payload.get("trace_id"),
                            },
                            observation=observation,
                        )
                    trace_emit("final_stored_memory", fact_key=fact_key, display_text=candidate.display_text,
                               owner_id=candidate.owner_id, predicate=candidate.predicate,
                               authority_rank=canonical_fact_service._authority_rank("conversation_extraction"), memory_type="canonical_fact")
                    _trace_validate(fact, candidate, fact_key)
                continue
            # Unsupported information is not promoted into the fact vocabulary.
            # A first-person declarative statement is retained as an existing
            # PROFILE_NOTE; third-party and general information remain ignored.
            if fact_normalizer._is_authenticated_user_reference(fact.subject, payload["user_id"]):
                with trace_stage("canonicalization", result="profile_note_or_ignore"):
                    redirected = profile_service.persist_unsupported_self_fact(
                        payload["user_id"], fact.content,
                        {"message_id": payload.get("job_id", "extraction"), "session_id": messages[-1].session_id if messages else None},
                        observation=observation,
                    )
                if redirected:
                    trace_emit("memory_worthiness", decision="STORE", reason="unsupported_self_fact_redirected_to_profile_note")
                    trace_emit("final_stored_memory", display_text=fact.content, owner_id=payload["user_id"],
                               predicate="PROFILE_NOTE", authority_rank=30, memory_type="profile_note")
                    _trace_validate(fact, None, None)
                    logging.info("Redirected unsupported self fact to PROFILE_NOTE for user_id=%s trace_id=%s", payload["user_id"], payload.get("trace_id"))
                    continue
            trace_emit("memory_worthiness", decision="IGNORE", reason="unsupported_or_non_user_fact")
            logging.info("Ignoring unsupported extracted fact for user_id=%s trace_id=%s", payload["user_id"], payload.get("trace_id"))


def _trace_validate(fact, candidate, fact_key) -> None:
    """Best-effort diagnostics only; all warnings are non-blocking."""
    with trace_stage("validation"):
        if candidate is None:
            if "?" in fact.content or any(word in fact.content.lower() for word in ("task", "todo", "please ")):
                trace_warning("profile_note_from_question_or_task", "PROFILE_NOTE evidence resembles a question or task")
            trace_emit("validation_result", status="completed", fact_key=None)
            return
        if not candidate.owner_id:
            trace_warning("missing_owner_id", "Canonical fact has no owner_id", fact_key=fact_key)
        if predicate_registry.definition(candidate.predicate) is None:
            trace_warning("unsupported_predicate", "Canonical fact uses an unsupported predicate", predicate=candidate.predicate)
        if not fact_normalizer._is_authenticated_user_reference(fact.subject, candidate.owner_id or "") and candidate.subject_key == f"person:{(candidate.owner_id or '').upper()}":
            trace_warning("third_party_promoted_to_user", "A non-first-person subject resolved to the authenticated user", subject=fact.subject)
        try:
            with neo4j.get_session() as session:
                user_nodes = session.run("MATCH (u:User {id:$owner_id}) RETURN count(u) AS count", owner_id=candidate.owner_id).single()["count"]
                if user_nodes > 1:
                    trace_warning("multiple_user_nodes", "More than one User node exists for the owner", count=user_nodes)
                names = session.run("MATCH (f:Fact {owner_id:$owner_id, predicate:'HAS_NAME', current:true}) RETURN count(f) AS count", owner_id=candidate.owner_id).single()["count"]
                if names > 1:
                    trace_warning("multiple_name_facts", "More than one current HAS_NAME fact exists", count=names)
                duplicates = session.run("MATCH (f:Fact {fact_key:$fact_key}) RETURN count(f) AS count", fact_key=fact_key).single()["count"]
                if duplicates > 1:
                    trace_warning("duplicate_canonical_fact", "More than one Fact node has the canonical key", fact_key=fact_key, count=duplicates)
                if candidate.predicate == "REPORTS_TO" and candidate.object_key:
                    reverse = session.run("MATCH (a:Entity {key:$subject_key})-[:REPORTS_TO]->(b:Entity {key:$object_key}) MATCH (b)-[:REPORTS_TO]->(a) RETURN count(*) AS count", subject_key=candidate.subject_key, object_key=candidate.object_key).single()["count"]
                    if reverse:
                        trace_warning("reports_to_both_directions", "REPORTS_TO exists in both directions", subject_key=candidate.subject_key, object_key=candidate.object_key)
        except Exception as error:
            trace_warning("validation_query_failed", "Validation query failed; memory processing continues", error_type=type(error).__name__)
        trace_emit("validation_result", status="completed", fact_key=fact_key)


def handle_vector_projection(payload: dict) -> None:
    with activate_trace(payload.get("trace_id")):
        with trace_stage("chroma_projection", operation=payload.get("operation", "upsert")):
            if payload.get("kind") == "fact":
                canonical_fact_service.project_vector(payload["fact_key"], payload.get("operation", "upsert"))
                trace_emit("chroma_projection_details", fact_key=payload["fact_key"], operation=payload.get("operation", "upsert"), kind="canonical_fact")
                return
            memory_service.project_vector(payload["memory_id"], payload.get("operation", "upsert"))
            trace_emit("chroma_projection_details", memory_id=payload["memory_id"], operation=payload.get("operation", "upsert"), kind="legacy_memory")


def handle_profile_observation(payload: dict) -> None:
    profile_service.process_message(payload)


def handle_enterprise_cache(payload: dict) -> None:
    enterprise_profile_cache_service.process(payload)


def handle_context_write_intent(payload: dict) -> None:
    """Execute a post-read intent through the existing worker-owned writers."""
    intent = ContextWriteIntent.model_validate(payload)
    if intent.kind == "enterprise_cache":
        records = [KnowledgeRecord.model_validate(record) for record in intent.payload["records"]]
        fields = [
            EnterpriseField(**field) if isinstance(field, dict) else field
            for field in (intent.payload.get("matched_fields") or [])
        ]
        enterprise_profile_cache_service.enqueue_records(
            intent.payload["user_id"], records, fields,
        )
        return
    if intent.kind == "knowledge_memory_refresh":
        memory = memory_service.get_memory_by_id(intent.payload["memory_id"])
        if memory is not None:
            memory_service.refresh_knowledge_memory(
                memory,
                intent.payload["content"],
                intent.payload["knowledge_reference"],
            )
        return
    raise ValueError(f"Unsupported context write intent: {intent.kind}")


def enterprise_cache_refresh_loop() -> None:
    while True:
        token = start_timing("worker", "enterprise-cache-refresh")
        outcome = "ok"
        try:
            with timed("worker.enterprise_cache_refresh"):
                enterprise_profile_cache_service.refresh_all()
        except Exception:
            outcome = "error"
            logging.exception("On-demand enterprise cache refresh failed")
        finally:
            finish_timing(token, outcome, stream="enterprise-cache-refresh", attempt=0)
        time.sleep(settings.ENTERPRISE_CACHE_REFRESH_INTERVAL_SECONDS)


def enterprise_sync_loop() -> None:
    """Run the optional full-workbook sync on the configured interval."""
    while True:
        try:
            # Workers may start before the API container's bootstrap command.
            # Migrations are idempotent, so each worker protects its own first
            # synchronization from that startup race.
            run_migrations()
            enterprise_sync_service.sync()
        except Exception:
            logging.exception("Enterprise snapshot synchronization failed")
        time.sleep(settings.ENTERPRISE_SYNC_INTERVAL_SECONDS)


def start_enterprise_sync() -> bool:
    """Start automatic workbook synchronization only when explicitly enabled."""
    if not settings.ENABLE_ENTERPRISE_SYNC:
        logging.info("Automatic enterprise synchronization is disabled")
        return False
    threading.Thread(target=enterprise_sync_loop, daemon=True, name="enterprise-sync").start()
    return True


def main() -> None:
    consumer = f"worker-{socket.gethostname()}"
    start_enterprise_sync()
    if settings.ENTERPRISE_ON_DEMAND_CACHE_ENABLED:
        threading.Thread(target=enterprise_cache_refresh_loop, daemon=True, name="enterprise-cache-refresh").start()
    # One process handles both queues, prioritizing extraction. A second worker
    # can be added later without changing message contracts.
    threading.Thread(target=job_queue.process_forever, args=("vector-projection", "vector-workers", consumer + "-vector", handle_vector_projection), daemon=True).start()
    threading.Thread(target=job_queue.process_forever, args=("profile-observation", "profile-workers", consumer + "-profile", handle_profile_observation), daemon=True).start()
    threading.Thread(target=job_queue.process_forever, args=("enterprise-cache", "enterprise-cache-workers", consumer + "-enterprise-cache", handle_enterprise_cache), daemon=True).start()
    threading.Thread(target=job_queue.process_forever, args=("context-write-intent", "context-write-intent-workers", consumer + "-context-write-intent", handle_context_write_intent), daemon=True).start()
    job_queue.process_forever("memory-extraction", "extraction-workers", consumer + "-extract", handle_extraction)


if __name__ == "__main__":
    main()
