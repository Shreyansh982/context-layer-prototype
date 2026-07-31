from datetime import datetime

from app.context_orchestrator import ContextOrchestrator
from app.models import KnowledgeRecord, MemoryFact, MemoryScope
from app.services.knowledge import KnowledgeSearchTrace
from app.router import RetrievalSource


def test_authoritative_knowledge_is_retrieved_even_when_personal_memory_exists(monkeypatch):
    memory = MemoryFact(
        id="memory-1", content="Employee E0004 uses laptop model LTP006.",
        scope=MemoryScope.USER, owner_id="E0004", agent_id="agent-a", confidence=1.0,
        timestamp=datetime.now(),
    )
    monkeypatch.setattr("app.context_orchestrator.memory_service.search", lambda **_kwargs: [memory])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.get_user_facts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.memory_service.get_company_memory", lambda: [])
    monkeypatch.setattr("app.context_orchestrator.conversation_service.get_conversation", lambda _user_id: [])
    record = KnowledgeRecord(sheet_name="IT", row_data={"EMP_ID": "E0004", "LAPTOP_MODEL": "LTP006"})
    monkeypatch.setattr(
        "app.context_orchestrator.knowledge_service.search_with_trace",
        lambda **_kwargs: ([record], KnowledgeSearchTrace(query_tokens=["laptop", "model"])),
    )
    monkeypatch.setattr("app.context_orchestrator.ContextOrchestrator._persist_knowledge_results", lambda *_args: None)

    context = ContextOrchestrator().build_context(
        "E0004", "agent-a", "What is my laptop model?",
        routes=[RetrievalSource.MEMORY, RetrievalSource.KNOWLEDGE],
    )

    assert context.memories == [memory]
    assert context.knowledge == [record]
