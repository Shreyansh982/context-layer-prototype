import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.config import settings
from app.models import ConversationMessage, ExtractedFact
from app.services.extraction import ExtractionService


FACT = {
    "content": "I work at JSPL.",
    "subject": "USER",
    "predicate": "WORKS_AT",
    "object": "JSPL",
    "object_type": "ORGANIZATION",
}
JSON_REPLY = json.dumps({"facts": [FACT]})


@pytest.mark.parametrize("reply,expected", [
    (JSON_REPLY, [ExtractedFact(**FACT)]),
    (f"```json\n{JSON_REPLY}\n```", [ExtractedFact(**FACT)]),
    ("not valid JSON", []),
], ids=["valid-json", "fenced-json", "invalid-json"])
def test_extract_uses_configured_model_and_parses_response(monkeypatch, reply, expected):
    create = Mock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]
    ))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr("app.services.extraction.Groq", lambda **_kwargs: client)
    monkeypatch.setattr(settings, "GROQ_MODEL", "configured-test-model")

    facts = ExtractionService().extract([
        ConversationMessage(
            role="user",
            content="I work at JSPL and report to Bala.",
            timestamp=datetime.now(timezone.utc),
        )
    ])

    assert facts == expected
    create.assert_called_once()
    assert create.call_args.kwargs["model"] == settings.GROQ_MODEL
    assert create.call_args.kwargs["response_format"] == {"type": "json_object"}
