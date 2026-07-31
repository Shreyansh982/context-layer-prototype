from app.context_orchestrator import ContextOrchestrator
from datetime import datetime
from app.models import ConversationMessage
from app.router import RetrievalSource
from app.services.knowledge import KnowledgeSearchTrace
import pytest


@pytest.mark.parametrize("routes", [
    [RetrievalSource.MEMORY],
    [RetrievalSource.KNOWLEDGE],
])
def test_context_always_attaches_conversation_for_retrieval_routes(monkeypatch, routes):
    history = [ConversationMessage(
        role="user", content="I am in Jindal Steel", timestamp=datetime.now()
    )]
    monkeypatch.setattr(
        "app.context_orchestrator.conversation_service.get_conversation",
        lambda _user_id: history,
    )
    monkeypatch.setattr(
        "app.context_orchestrator.knowledge_service.search_with_trace",
        lambda **_kwargs: ([], KnowledgeSearchTrace(query_tokens=[])),
    )
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.search",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.get_user_facts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.get_company_memory",
        lambda: [],
    )

    context = ContextOrchestrator().build_context(
        user_id="E0004",
        agent_id="agent-a",
        query="What is the leave policy?",
        routes=routes,
    )

    assert context.conversation == history


def test_memory_search_uses_configured_top_k(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.context_orchestrator.settings.TOP_K", 7)
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.search",
        lambda **kwargs: captured.update(kwargs) or [],
    )
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.canonical_fact_service.get_user_facts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.context_orchestrator.memory_service.get_company_memory", lambda: [])
    monkeypatch.setattr(
        "app.context_orchestrator.conversation_service.get_conversation",
        lambda _user_id: [],
    )

    ContextOrchestrator().build_context(
        user_id="E0004",
        agent_id="agent-a",
        query="What is my preference?",
        routes=[RetrievalSource.MEMORY],
    )

    assert captured["top_k"] == 7
