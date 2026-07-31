import json

from app.models import ExtractedFact
from app.worker import handle_extraction, handle_vector_projection


def _payload(trace_id="trace-1"):
    return {
        "job_id": "job-1", "user_id": "E0001", "agent_id": "agent-a", "trace_id": trace_id,
        "trace_original_message": "I work at Jindal Steel.", "trace_message_id": "message-1",
        "messages": [{"id": "message-1", "role": "user", "content": "I work at Jindal Steel.", "timestamp": "2026-01-01T00:00:00"}],
    }


def test_extraction_trace_disabled_does_not_write_trace(monkeypatch, tmp_path):
    monkeypatch.setattr("app.worker.settings.EXTRACTION_TRACE", False)
    monkeypatch.setattr("app.worker.extraction_service.extract", lambda _messages: [])
    monkeypatch.setattr("app.extraction_trace.settings.EXTRACTION_TRACE_DIRECTORY", str(tmp_path))

    handle_extraction(_payload())

    assert list(tmp_path.glob("*")) == []


def test_extraction_trace_records_pipeline_and_projection(monkeypatch, tmp_path):
    monkeypatch.setattr("app.worker.settings.EXTRACTION_TRACE", True)
    monkeypatch.setattr("app.extraction_trace.settings.EXTRACTION_TRACE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(
        "app.worker.extraction_service.extract",
        lambda _messages: [ExtractedFact(content="I work at Jindal Steel.", subject="I", predicate="WORKS_AT", object="Jindal Steel", object_type="ORGANIZATION")],
    )
    monkeypatch.setattr("app.worker.canonical_fact_service.upsert", lambda *_args, **_kwargs: "fact-1")
    monkeypatch.setattr("app.worker._trace_validate", lambda *_args: None)
    monkeypatch.setattr("app.worker.canonical_fact_service.project_vector", lambda *_args: None)

    handle_extraction(_payload())
    handle_vector_projection({"kind": "fact", "fact_key": "fact-1", "operation": "upsert", "trace_id": "trace-1"})

    events = [json.loads(line) for line in (tmp_path / "trace-1.jsonl").read_text(encoding="utf-8").splitlines()]
    stages = {event["stage"] for event in events}
    assert {"pipeline_start", "extraction", "extracted_fact", "canonicalization", "canonical_fact", "memory_worthiness", "graph_write", "final_stored_memory", "chroma_projection"} <= stages
    assert next(event for event in events if event["stage"] == "pipeline_start")["original_user_message"] == "I work at Jindal Steel."


def test_trace_validation_warning_never_crashes_processing(monkeypatch, tmp_path):
    monkeypatch.setattr("app.worker.settings.EXTRACTION_TRACE", True)
    monkeypatch.setattr("app.extraction_trace.settings.EXTRACTION_TRACE_DIRECTORY", str(tmp_path))
    monkeypatch.setattr("app.worker.extraction_service.extract", lambda _messages: [ExtractedFact(content="What is GraphDB?", subject="USER")])
    monkeypatch.setattr("app.worker.profile_service.persist_unsupported_self_fact", lambda *_args, **_kwargs: True)

    handle_extraction(_payload("trace-warning"))

    events = [json.loads(line) for line in (tmp_path / "trace-warning.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event.get("code") == "profile_note_from_question_or_task" for event in events)
