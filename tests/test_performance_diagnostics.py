import json
import logging

from app import performance


def test_request_timing_summary_and_json_report(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="context_layer.performance")
    monkeypatch.setattr(performance.settings, "PERFORMANCE_INSTRUMENTATION_ENABLED", True)
    monkeypatch.setattr(performance.settings, "PERFORMANCE_SUMMARY_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(performance.settings, "PERFORMANCE_SAMPLE_LIMIT", 20)
    performance._reporters.clear()

    token = performance.start_timing("api", "request-123", "request-123")
    with performance.timed("routing"):
        pass
    with performance.timed("llm.answer"):
        pass
    summary = performance.finish_timing(token, "ok", path="/query", status=200)

    assert summary["event"] == "request_timing_summary"
    assert {"routing", "llm.answer", "request.total"} <= set(summary["stages"])
    assert any("request_timing_summary" in record.message for record in caplog.records)

    performance._reporter("api").flush()
    reports = list(tmp_path.glob("api-performance-summary-*.jsonl"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8").strip())
    assert report["slowest_operation_id"] == "request-123"
    assert report["stages"]["routing"]["count"] == 1
    assert "p95_ms" in report["stages"]["llm.answer"]


def test_disabled_instrumentation_is_a_noop(monkeypatch):
    monkeypatch.setattr(performance.settings, "PERFORMANCE_INSTRUMENTATION_ENABLED", False)
    assert performance.start_timing("api", "request-disabled") is None
    with performance.timed("routing"):
        pass
