"""
Context Orchestrator

Collects context from different services
based on the routing decision.
"""

import json
import re

from app.models import ContextPackage, ContextWriteIntent, MemoryScope
from app.config import settings
from app.router import RetrievalSource, RoutingDecision, query_router
from app.routing_trace import content_details, log_routing_event
from app.services.conversation import conversation_service
from app.services.knowledge import knowledge_service
from app.services.memory import memory_service
from app.services.facts import canonical_fact_service
from app.services.profile import profile_service

class ContextOrchestrator:
    """
    Retrieves context required to answer
    a user query.
    """
    def build_context(
        self,
        user_id: str,
        agent_id: str,
        query: str,
        routes: list[RetrievalSource] | None = None,
        decision: RoutingDecision | None = None,
    ) -> ContextPackage:
        decision = decision or (query_router.analyze(query) if routes is None else None)
        routes = routes if routes is not None else decision.selected_routes
        context = ContextPackage()

        # Active response preferences are a tiny indexed graph lookup and
        # apply to every answer, independently of memory routing.
        context.profile, context.profile_version = profile_service.get_profile(user_id)

        # Memory
        
        if RetrievalSource.MEMORY in routes:
            context.memories = self._retrieve_user_memory(user_id, agent_id, query, decision)
            context.memories = self._verify_knowledge_memories(
                user_id, context.memories, context.write_intents,
            )
            log_routing_event(
                "memory_retrieval",
                executed=True,
                memories=[{"id": memory.id, "source": memory.source, "scope": memory.scope.value, "content": content_details(memory.content)} for memory in context.memories],
            )
        else:
            log_routing_event("memory_retrieval", executed=False, reason="route_not_selected")

        if RetrievalSource.MEMORY in routes or RetrievalSource.KNOWLEDGE in routes:
            # Company facts are global context for enterprise questions, not a
            # fuzzy-search-only result. General questions never reach here.
            company_memories = memory_service.get_company_memory()
            known_ids = {memory.id for memory in context.memories}
            context.memories.extend(
                memory for memory in company_memories if memory.id not in known_ids
            )

        # Knowledge is authoritative for a direct workbook field. It must not
        # be skipped merely because semantic memory found a personal fact.
        if RetrievalSource.KNOWLEDGE in routes:
            context.knowledge, knowledge_trace = knowledge_service.search_with_trace(
                query=query,
                requesting_user_id=user_id,
                field_matches=decision.enterprise_fields if decision else None,
                subject_is_authenticated_user=(decision.subject == "authenticated_user") if decision else True,
            )
            cache_intent = self._persist_knowledge_results(
                user_id, agent_id, context.knowledge,
                decision.enterprise_fields if decision else [],
                bool(re.search(r"\b(?:my\s+)?(?:record|profile)\b", query, re.I)),
            )
            if cache_intent is not None:
                context.write_intents.append(cache_intent)
            log_routing_event(
                "knowledge_retrieval",
                executed=True,
                query_tokens=knowledge_trace.query_tokens,
                matched_fields=[{"sheet": field.sheet_name, "field": field.column_name} for field in knowledge_trace.matched_fields],
                returned_records=knowledge_trace.returned_records,
            )
        else:
            log_routing_event("knowledge_retrieval", executed=False, reason="route_not_selected")

        # Conversation is local, cheap, and needed to resolve natural follow-ups.
        context.conversation = conversation_service.get_conversation(user_id)
        log_routing_event(
            "context_assembled",
            conversation=[{"role": message.role, "content": content_details(message.content)} for message in context.conversation],
            memory_count=len(context.memories),
            knowledge_records=[{"sheet": record.sheet_name, "fields": sorted(record.row_data.keys())} for record in context.knowledge],
        )
        return context

    def _retrieve_user_memory(self, user_id: str, agent_id: str, query: str, decision: RoutingDecision | None = None):
        """Use complete retrieval for the router's explicit-memory intent."""
        legacy = []
        decision = decision or query_router.analyze(query)
        complete_profile_request = "complete_profile_request" in decision.reasons.get(RetrievalSource.MEMORY, [])
        if complete_profile_request:
            legacy = memory_service.get_user_memory(user_id, agent_id)
            canonical = canonical_fact_service.get_owner_facts(user_id) if settings.CANONICAL_FACTS_ENABLED else []
        else:
            legacy = memory_service.search(
                query=query,
                owner_id=user_id,
                agent_id=agent_id,
                top_k=settings.TOP_K,
            )
            canonical = canonical_fact_service.search(query, user_id, settings.TOP_K) if settings.CANONICAL_FACTS_ENABLED else []
            # Projection jobs are asynchronous.  A freshly extracted fact
            # must still be visible before Chroma has caught up.
            if settings.CANONICAL_FACTS_ENABLED and not canonical:
                canonical = canonical_fact_service.get_user_facts(user_id)
        seen = {memory.id for memory in legacy}
        return legacy + [memory for memory in canonical if memory.id not in seen]

    def _persist_knowledge_results(
        self,
        user_id: str,
        agent_id: str,
        records,
        matched_fields=None,
        cache_full_record: bool = False,
    ) -> ContextWriteIntent | None:
        """Describe a cache write without executing it during a read.

        The existing enterprise-cache service remains the owner of queueing
        and persistence.  Phase 1 intentionally returns an internal intent
        instead, so a context read cannot enqueue Redis work or mutate a cache.
        """
        del agent_id
        if records and (matched_fields or cache_full_record):
            return ContextWriteIntent(
                kind="enterprise_cache",
                payload={
                    "user_id": user_id,
                    "records": records,
                    "matched_fields": matched_fields if not cache_full_record else None,
                },
            )
        return None

    def _verify_knowledge_memories(
        self,
        user_id: str,
        memories,
        write_intents: list[ContextWriteIntent] | None = None,
    ):
        """Verify Excel-backed memory without mutating durable state.

        A current workbook value is used immediately in the returned context.
        If its durable cache is stale, a refresh intent is returned to the
        caller; no Neo4j, Chroma, or Redis mutation occurs on this read path.
        """
        verified = []
        for memory in memories:
            if memory.source != "knowledge_lookup":
                verified.append(memory)
                continue
            reference = memory.knowledge_reference or self._legacy_reference(memory.content)
            record = knowledge_service.verify_reference(reference, user_id) if reference else None
            if record is None:
                continue
            current_content = knowledge_service.describe_record(record)
            current_reference = knowledge_service.reference_for(record)
            if memory.content != current_content or memory.knowledge_reference != current_reference:
                if write_intents is not None:
                    write_intents.append(ContextWriteIntent(
                        kind="knowledge_memory_refresh",
                        payload={
                            "memory_id": memory.id,
                            "content": current_content,
                            "knowledge_reference": current_reference,
                        },
                    ))
                # Preserve the existing context result (which used the
                # refreshed content) without changing the stored memory.
                memory = memory.model_copy(update={
                    "content": current_content,
                    "knowledge_reference": current_reference,
                })
            verified.append(memory)
        return verified

    def _legacy_reference(self, content: str) -> str | None:
        """Allow pre-migration JSON memories to gain verification on first use."""
        try:
            parsed = json.loads(content)
            if {"sheet", "row"} <= parsed.keys():
                return json.dumps(parsed, default=str, sort_keys=True)
        except (json.JSONDecodeError, AttributeError):
            pass
        return None

context_orchestrator = ContextOrchestrator()
