import json
import logging

from app.llm import LLMService, UNAVAILABLE_ANSWER


class TransientError(Exception):
    code = 429


class PermanentError(Exception):
    code = 400


class FakeProvider:
    def __init__(self, name, outcomes):
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, prompt, query):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def check_ready(self):
        return None


def test_gemini_primary_returns_without_calling_groq():
    gemini = FakeProvider("gemini", ["Gemini answer"])
    groq = FakeProvider("groq", ["Groq answer"])

    service = LLMService(gemini=gemini, groq=groq, primary_name="gemini")

    assert service.generate("context", "question") == "Gemini answer"
    assert gemini.calls == 1
    assert groq.calls == 0


def test_transient_gemini_failure_retries_once_then_falls_back_to_groq(monkeypatch, caplog):
    gemini = FakeProvider("gemini", [TransientError(), TransientError()])
    groq = FakeProvider("groq", ["Groq fallback answer"])
    monkeypatch.setattr("app.llm.time.sleep", lambda _delay: None)
    caplog.set_level(logging.INFO, logger="app.llm")

    service = LLMService(gemini=gemini, groq=groq, primary_name="gemini")

    assert service.generate("context", "question") == "Groq fallback answer"
    assert gemini.calls == 2
    assert groq.calls == 1
    events = [json.loads(record.message)["event"] for record in caplog.records if record.message.startswith("{")]
    assert {"llm_provider_selected", "llm_provider_retry", "llm_fallback_activated", "llm_provider_call"} <= set(events)


def test_both_provider_failures_return_graceful_answer(monkeypatch):
    gemini = FakeProvider("gemini", [TransientError(), TransientError()])
    groq = FakeProvider("groq", [PermanentError()])
    monkeypatch.setattr("app.llm.time.sleep", lambda _delay: None)

    service = LLMService(gemini=gemini, groq=groq, primary_name="gemini")

    assert service.generate("context", "question") == UNAVAILABLE_ANSWER


def test_non_transient_gemini_failure_uses_fallback_without_retry():
    gemini = FakeProvider("gemini", [PermanentError()])
    groq = FakeProvider("groq", ["Groq fallback answer"])

    service = LLMService(gemini=gemini, groq=groq, primary_name="gemini")

    assert service.generate("context", "question") == "Groq fallback answer"
    assert gemini.calls == 1
    assert groq.calls == 1
