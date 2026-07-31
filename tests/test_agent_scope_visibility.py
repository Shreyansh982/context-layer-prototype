from datetime import datetime

from app.context_orchestrator import ContextOrchestrator
from app.models import MemoryFact, MemoryScope
from app.router import RetrievalSource


def test_user_facing_context_never_calls_agent_memory(monkeypatch):
    user_memory = MemoryFact(
        id="user-1", content="User prefers concise answers.", scope=MemoryScope.USER,
        owner_id="E0005", agent_id="agent-a", confidence=1.0, timestamp=datetime.now(),
    )
    monkeypatch.setattr("app.context_orchestrator.memory_service.search", lambda **_kwargs: [user_memory])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.get_user_facts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.memory_service.get_company_memory", lambda: [])
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.get_agent_memory",
        lambda _agent_id: (_ for _ in ()).throw(AssertionError("AGENT memory leaked to request path")),
    )
    monkeypatch.setattr("app.context_orchestrator.conversation_service.get_conversation", lambda _user_id: [])

    context = ContextOrchestrator().build_context(
        "E0005", "agent-a", "What is my preference?", routes=[RetrievalSource.MEMORY]
    )

    assert context.memories == [user_memory]
    assert all(memory.scope != MemoryScope.AGENT for memory in context.memories)
