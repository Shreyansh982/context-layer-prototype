from app import api
from datetime import datetime
from app.models import ContextPackage, ConversationMessage, QueryRequest
from app.auth import Principal
from types import SimpleNamespace


def test_generic_follow_up_uses_prior_conversation_in_prompt(monkeypatch):
    monkeypatch.setattr(api.safety_policy, "refusal_for", lambda _query: None)
    monkeypatch.setattr(api.application_orchestrator, "process_message", lambda **_kwargs: None)
    monkeypatch.setattr(
        api.context_orchestrator,
        "build_context",
        lambda **_kwargs: ContextPackage(conversation=[
            ConversationMessage(
                role="user",
                content="Tell me Jindal Steel's notice period",
                timestamp=datetime.now(),
            ),
            ConversationMessage(role="assistant", content="It is 30 days.", timestamp=datetime.now()),
        ]),
    )
    captured = {}
    monkeypatch.setattr(
        api.llm_service,
        "generate",
        lambda prompt, query: captured.update(prompt=prompt, query=query) or "Acknowledged.",
    )

    response = api.query(QueryRequest(user_id="E0004", agent_id="agent-a", query="Okay"), Principal("E0004"))

    assert response.answer == "Acknowledged."
    assert captured["query"] == "Okay"
    assert "Tell me Jindal Steel's notice period" in captured["prompt"]
    assert "It is 30 days." in captured["prompt"]
    assert response.context_used == ["conversation"]


def test_empty_history_uses_contextual_prompt_without_external_retrieval(monkeypatch):
    monkeypatch.setattr(api.safety_policy, "refusal_for", lambda _query: None)
    monkeypatch.setattr(api.application_orchestrator, "process_message", lambda **_kwargs: None)
    monkeypatch.setattr(api.query_router, "analyze", lambda _query: SimpleNamespace(selected_routes=[]))
    captured = {}
    monkeypatch.setattr(
        api.context_orchestrator,
        "build_context",
        lambda **kwargs: captured.update(kwargs=kwargs) or ContextPackage(),
    )
    monkeypatch.setattr(
        api.llm_service,
        "generate",
        lambda prompt, query: captured.update(prompt=prompt, query=query) or "Gravity answer",
    )

    response = api.query(QueryRequest(user_id="E0004", agent_id="agent-a", query="Explain gravity"), Principal("E0004"))

    assert captured["kwargs"]["routes"] == []
    assert "No conversation context." in captured["prompt"]
    assert response.context_used == []
