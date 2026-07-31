from datetime import datetime

from app.context_orchestrator import ContextOrchestrator
from app.models import KnowledgeRecord, MemoryFact, MemoryScope


def knowledge_memory(content="Employee E0004 has a leave balance of 22."):
    return MemoryFact(
        id="memory-1", content=content, scope=MemoryScope.USER, owner_id="E0004",
        agent_id="agent-a", confidence=1.0, timestamp=datetime.now(), source="knowledge_lookup",
        knowledge_reference='{"sheet":"HR","row":{"EMP_ID":"E0004"}}',
    )


def test_verification_returns_refresh_intent_without_mutating_memory(monkeypatch):
    memory = knowledge_memory()
    current = KnowledgeRecord(sheet_name="HR", row_data={"EMP_ID": "E0004", "LEAVE_BAL": 18})
    monkeypatch.setattr("app.context_orchestrator.knowledge_service.verify_reference", lambda *_args: current)
    monkeypatch.setattr("app.context_orchestrator.knowledge_service.describe_record", lambda _record: "Employee E0004 has a leave balance of 18.")
    monkeypatch.setattr("app.context_orchestrator.knowledge_service.reference_for", lambda _record: "current-reference")
    monkeypatch.setattr(
        "app.context_orchestrator.memory_service.refresh_knowledge_memory",
        lambda *_args: (_ for _ in ()).throw(AssertionError("read path must not refresh memory")),
    )

    intents = []
    verified = ContextOrchestrator()._verify_knowledge_memories("E0004", [memory], intents)

    assert memory.content == "Employee E0004 has a leave balance of 22."
    assert verified[0].content == "Employee E0004 has a leave balance of 18."
    assert verified[0].knowledge_reference == "current-reference"
    assert intents[0].kind == "knowledge_memory_refresh"
    assert intents[0].payload == {
        "memory_id": "memory-1",
        "content": "Employee E0004 has a leave balance of 18.",
        "knowledge_reference": "current-reference",
    }


def test_verification_discards_missing_or_unauthorized_workbook_memory(monkeypatch):
    monkeypatch.setattr("app.context_orchestrator.knowledge_service.verify_reference", lambda *_args: None)

    verified = ContextOrchestrator()._verify_knowledge_memories("E0004", [knowledge_memory()])

    assert verified == []
