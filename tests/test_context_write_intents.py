"""Regression coverage for post-read deferred ContextWriteIntent dispatch."""

from app import api, worker
from app.auth import Principal
from app.memory_orchestrator import ApplicationOrchestrator
from app.models import ContextPackage, ContextWriteIntent, KnowledgeRecord, QueryRequest
from app.services.jobs import RedisJobQueue
from app.services.knowledge import EnterpriseField


def _enterprise_cache_intent() -> ContextWriteIntent:
    return ContextWriteIntent(
        kind="enterprise_cache",
        payload={
            "user_id": "E0004",
            "records": [KnowledgeRecord(sheet_name="HR", row_data={"EMP_ID": "E0004", "LEAVE_BAL": 18})],
            "matched_fields": [EnterpriseField("HR", "LEAVE_BAL", "leave balance")],
        },
    )


def test_api_queues_deferred_intents_after_context_without_changing_response(monkeypatch):
    events = []
    context = ContextPackage(write_intents=[_enterprise_cache_intent()])
    monkeypatch.setattr(api.safety_policy, "refusal_for", lambda _query: None)
    monkeypatch.setattr(api.application_orchestrator, "process_message", lambda **_kwargs: None)
    monkeypatch.setattr(api.query_router, "analyze", lambda _query: type("Decision", (), {"selected_routes": []})())
    monkeypatch.setattr(
        api.context_orchestrator,
        "build_context",
        lambda **_kwargs: events.append("context") or context,
    )
    monkeypatch.setattr(
        api.application_orchestrator,
        "dispatch_context_write_intents",
        lambda intents: events.append(("dispatch", list(intents))) or 1,
    )
    monkeypatch.setattr(
        api.llm_service,
        "generate",
        lambda _prompt, query: events.append("llm") or "Answer",
    )

    response = api.query(
        QueryRequest(user_id="E0004", agent_id="agent-a", query="What is my leave balance?"),
        Principal("E0004"),
    )

    assert events[0] == "context"
    assert events[1][0] == "dispatch"
    assert events[1][1] == context.write_intents
    assert events[2] == "llm"
    assert response.model_dump() == {"answer": "Answer", "context_used": []}


def test_dispatcher_coalesces_duplicate_intents_across_repeated_reads(monkeypatch):
    queued = set()

    def enqueue_once(_stream, _payload, *, idempotency_key, ttl_seconds):
        assert ttl_seconds > 0
        if idempotency_key in queued:
            return None
        queued.add(idempotency_key)
        return "1-0"

    monkeypatch.setattr("app.memory_orchestrator.job_queue.enqueue_once", enqueue_once)
    intent = _enterprise_cache_intent()
    dispatcher = ApplicationOrchestrator()

    assert dispatcher.dispatch_context_write_intents([intent, intent]) == 1
    assert dispatcher.dispatch_context_write_intents([intent]) == 0
    assert len(queued) == 1


def test_enqueue_once_claims_and_queues_a_deferred_intent_atomically():
    class RedisSpy:
        def __init__(self):
            self.claims = set()
            self.entries = []

        def eval(self, script, key_count, key, stream, ttl_seconds, payload, attempt):
            assert "SET" in script and "XADD" in script
            assert key_count == 2
            assert ttl_seconds > 0
            if key in self.claims:
                return ""
            self.claims.add(key)
            self.entries.append((stream, payload, attempt))
            return "1-0"

    client = RedisSpy()
    queue = RedisJobQueue(client=client)

    assert queue.enqueue_once(
        "context-write-intent", {"kind": "enterprise_cache"},
        idempotency_key="context-layer:context-write-intent:one", ttl_seconds=60,
    ) == "1-0"
    assert queue.enqueue_once(
        "context-write-intent", {"kind": "enterprise_cache"},
        idempotency_key="context-layer:context-write-intent:one", ttl_seconds=60,
    ) is None
    assert len(client.entries) == 1


def test_worker_dispatches_enterprise_intent_to_existing_async_cache_queue(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker.enterprise_profile_cache_service,
        "enqueue_records",
        lambda user_id, records, fields: calls.append((user_id, records, fields)),
    )
    monkeypatch.setattr(
        worker.enterprise_profile_cache_service,
        "process",
        lambda _payload: (_ for _ in ()).throw(AssertionError("cache writes must stay on the enterprise-cache worker")),
    )

    worker.handle_context_write_intent(_enterprise_cache_intent().model_dump(mode="json"))

    user_id, records, fields = calls[0]
    assert user_id == "E0004"
    assert records == [KnowledgeRecord(sheet_name="HR", row_data={"EMP_ID": "E0004", "LEAVE_BAL": 18})]
    assert fields == [EnterpriseField("HR", "LEAVE_BAL", "leave balance")]


def test_worker_executes_knowledge_refresh_through_existing_validated_writer(monkeypatch):
    memory = object()
    calls = []
    monkeypatch.setattr(worker.memory_service, "get_memory_by_id", lambda memory_id: memory if memory_id == "memory-1" else None)
    monkeypatch.setattr(
        worker.memory_service,
        "refresh_knowledge_memory",
        lambda current, content, reference: calls.append((current, content, reference)),
    )
    intent = ContextWriteIntent(
        kind="knowledge_memory_refresh",
        payload={"memory_id": "memory-1", "content": "Current value", "knowledge_reference": "reference"},
    )

    worker.handle_context_write_intent(intent.model_dump(mode="json"))

    assert calls == [(memory, "Current value", "reference")]
