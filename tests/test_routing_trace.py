from app.config import settings
from app.routing_trace import content_details


def test_routing_content_is_redacted_by_default(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG_ROUTING_CONTENT", False)
    details = content_details("private employee data")
    assert details["length"] == 21
    assert "content" not in details


def test_routing_content_is_available_only_with_explicit_debug_flag(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG_ROUTING_CONTENT", True)
    assert content_details("local debug only")["content"] == "local debug only"
