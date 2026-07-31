"""Regression coverage for Phase 1: context assembly is read-only."""

from datetime import datetime

from app.context_orchestrator import ContextOrchestrator
from app.models import KnowledgeRecord, MemoryFact, MemoryScope
from app.router import RetrievalSource, RoutingDecision
from app.services.knowledge import EnterpriseField, KnowledgeSearchTrace


def _knowledge_memory() -> MemoryFact:
    return MemoryFact(
        id="knowledge-memory-1",
        content="Employee E0004 has a leave balance of 22.",
        scope=MemoryScope.USER,
        owner_id="E0004",
        agent_id="agent-a",
        confidence=1.0,
        timestamp=datetime(2026, 1, 1),
        source="knowledge_lookup",
        knowledge_reference='{"sheet":"HR","row":{"EMP_ID":"E0004"}}',
    )


def _decision(field: EnterpriseField) -> RoutingDecision:
    return RoutingDecision(
        normalized_query="what is my leave balance",
        subject="authenticated_user",
        matched_memory_patterns=[],
        matched_enterprise_terms=["leave"],
        enterprise_fields=[field],
        personal_memory_fallback=False,
        candidate_confidence={
            RetrievalSource.MEMORY: 1.0,
            RetrievalSource.KNOWLEDGE: 1.0,
            RetrievalSource.CONVERSATION: 1.0,
        },
        selected_routes=[RetrievalSource.MEMORY, RetrievalSource.KNOWLEDGE, RetrievalSource.CONVERSATION],
        reasons={},
    )


def test_context_reads_are_repeatable_and_never_execute_write_paths(monkeypatch):
    """A stale cache can yield an intent, but must never mutate during a read."""
    field = EnterpriseField("HR", "LEAVE_BAL", "leave balance")
    record = KnowledgeRecord(sheet_name="HR", row_data={"EMP_ID": "E0004", "LEAVE_BAL": 18})
    original_memory = _knowledge_memory()
    calls = []

    def forbidden(name):
        def _raise(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"read path attempted {name}")
        return _raise

    monkeypatch.setattr("app.context_orchestrator.profile_service.get_profile", lambda _user_id: ([], 41))
    monkeypatch.setattr("app.context_orchestrator.memory_service.search", lambda **_kwargs: [original_memory])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.get_user_facts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.memory_service.get_company_memory", lambda: [])
    monkeypatch.setattr("app.context_orchestrator.conversation_service.get_conversation", lambda _user_id: [])
    monkeypatch.setattr("app.context_orchestrator.knowledge_service.verify_reference", lambda *_args: record)
    monkeypatch.setattr("app.context_orchestrator.knowledge_service.describe_record", lambda _record: "Employee E0004 has a leave balance of 18.")
    monkeypatch.setattr("app.context_orchestrator.knowledge_service.reference_for", lambda _record: "current-reference")
    monkeypatch.setattr(
        "app.context_orchestrator.knowledge_service.search_with_trace",
        lambda **_kwargs: ([record], KnowledgeSearchTrace(query_tokens=["leave", "balance"], matched_fields=[field])),
    )

    # These are the write boundaries previously reached from context assembly:
    # Neo4j + Chroma via memory refresh, and Redis/cache work via enqueue.
    monkeypatch.setattr("app.context_orchestrator.memory_service.refresh_knowledge_memory", forbidden("neo4j/chroma refresh"))
    monkeypatch.setattr("app.services.enterprise_cache.enterprise_profile_cache_service.enqueue_records", forbidden("redis cache enqueue"))
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.project_vector", forbidden("chroma projection"))

    orchestrator = ContextOrchestrator()
    first = orchestrator.build_context(
        "E0004", "agent-a", "What is my leave balance?",
        routes=[RetrievalSource.MEMORY, RetrievalSource.KNOWLEDGE], decision=_decision(field),
    )
    second = orchestrator.build_context(
        "E0004", "agent-a", "What is my leave balance?",
        routes=[RetrievalSource.MEMORY, RetrievalSource.KNOWLEDGE], decision=_decision(field),
    )

    # The public context remains stable, and profile reads never increment it.
    assert first.model_dump() == second.model_dump()
    assert first.profile_version == second.profile_version == 41
    assert original_memory.content == "Employee E0004 has a leave balance of 22."
    assert first.memories[0].content == "Employee E0004 has a leave balance of 18."

    # Deferred work is visible internally, but it is deliberately not executed.
    assert [intent.kind for intent in first.write_intents] == [
        "knowledge_memory_refresh", "enterprise_cache",
    ]
    assert first.write_intents == second.write_intents
    assert calls == []
