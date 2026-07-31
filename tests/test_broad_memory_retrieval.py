from datetime import datetime

from app.context_orchestrator import ContextOrchestrator
from app.models import MemoryFact, MemoryScope
from app.router import RetrievalSource


def _memory(memory_id: str, content: str) -> MemoryFact:
    return MemoryFact(
        id=memory_id, content=content, scope=MemoryScope.USER, owner_id="E0005", agent_id="agent-a",
        confidence=1.0, timestamp=datetime.now(),
    )


def test_broad_self_query_uses_uncapped_graph_memory(monkeypatch):
    all_memories = [
        _memory("1", "User works in IT."),
        _memory("2", "User reports to Gyan Gupta."),
        _memory("3", "User's colleague is Asha."),
    ]
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.get_user_memory",
        lambda owner_id, agent_id: all_memories,
    )
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.search",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("ranked search must not run")),
    )
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.get_owner_facts", lambda _user_id: [])
    monkeypatch.setattr("app.context_orchestrator.memory_service.get_company_memory", lambda: [])
    monkeypatch.setattr("app.context_orchestrator.conversation_service.get_conversation", lambda _id: [])

    context = ContextOrchestrator().build_context(
        "E0005", "agent-a", "Where do I work, whom do I report to and who are my colleagues?",
        routes=[RetrievalSource.MEMORY],
    )

    assert context.memories == all_memories


def test_explicit_memory_queries_always_use_complete_retrieval(monkeypatch):
    orchestrator = ContextOrchestrator()
    complete = [_memory("1", "User works in IT."), _memory("2", "Gyan Gupta is VP of IT.")]
    monkeypatch.setattr("app.context_orchestrator.memory_service.get_user_memory", lambda *_args: complete)
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.get_owner_facts", lambda _user_id: [])
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.search",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("ranked search must not run")),
    )

    for query in (
        "Retrieve all my memories",
        "Show my memories",
        "List everything you know about me",
        "Tell me about myself",
    ):
        assert orchestrator._retrieve_user_memory("E0005", "agent-a", query) == complete
