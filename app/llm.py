"""Provider-neutral answer generation with Gemini primary and Groq fallback."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

import httpx
from groq import Groq

from app.config import settings
from app.errors import DependencyUnavailable
from app.performance import timed
from app.resilience import retry_external

try:  # Imported lazily at runtime so tests can use fake providers without the SDK.
    from google import genai
except ImportError:  # pragma: no cover - exercised only before dependency installation
    genai = None


logger = logging.getLogger(__name__)
UNAVAILABLE_ANSWER = "Answer generation is temporarily unavailable. Please try again shortly."


class LLMProvider(Protocol):
    """The only provider contract used by the answer-generation service."""

    name: str

    def generate(self, prompt: str, query: str) -> str: ...

    def check_ready(self) -> None: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, client: Any | None = None, model: str | None = None):
        self._client = client
        self.model = model or settings.GEMINI_MODEL

    @property
    def client(self):
        if self._client is None:
            if genai is None:
                raise RuntimeError("google-genai is not installed")
            # SDK retries are disabled because LLMService owns the one retry
            # and the Gemini-to-Groq fallback decision.
            self._client = genai.Client(
                api_key=settings.GEMINI_API_KEY,
                http_options={
                    "timeout": int(settings.LLM_TIMEOUT_SECONDS * 1000),
                    "retry_options": {"attempts": 0},
                },
            )
        return self._client

    def generate(self, prompt: str, query: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=query[:settings.MAX_QUERY_CHARACTERS],
            config={
                "system_instruction": prompt[:settings.MAX_PROMPT_CHARACTERS],
                "temperature": 0,
            },
        )
        return (response.text or "").strip()

    def check_ready(self) -> None:
        self.client.models.list()


class GroqProvider:
    name = "groq"

    def __init__(self, client: Any | None = None, model: str | None = None):
        self._client = client
        self.model = model or settings.GROQ_MODEL

    @property
    def client(self):
        if self._client is None:
            self._client = Groq(
                api_key=settings.GROQ_API_KEY,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_retries=0,
            )
        return self._client

    def generate(self, prompt: str, query: str) -> str:
        response = retry_external("Groq", lambda: self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": prompt[:settings.MAX_PROMPT_CHARACTERS]},
                {"role": "user", "content": query[:settings.MAX_QUERY_CHARACTERS]},
            ],
        ))
        return (response.choices[0].message.content or "").strip()

    def check_ready(self) -> None:
        self.client.models.list()


def _status_code(error: Exception) -> int | None:
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if callable(value):
            value = value()
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_transient(error: Exception) -> bool:
    """Only retry Gemini for timeout, rate-limit, and server-side failures."""
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return True
    status = _status_code(error)
    return status == 429 or (status is not None and 500 <= status <= 599)


class LLMService:
    """Stable application boundary: callers use only :meth:`generate`."""

    def __init__(
        self,
        gemini: LLMProvider | None = None,
        groq: LLMProvider | None = None,
        primary_name: str | None = None,
    ):
        self.providers: dict[str, LLMProvider] = {
            "gemini": gemini or GeminiProvider(),
            "groq": groq or GroqProvider(),
        }
        selected = (primary_name or settings.PRIMARY_LLM).strip().lower()
        if selected not in self.providers:
            raise ValueError("PRIMARY_LLM must be 'gemini' or 'groq'")
        self.primary_name = selected
        self.fallback_name = "groq" if selected == "gemini" else "gemini"

    @property
    def client(self):
        """Compatibility access for the existing readiness endpoint."""
        return getattr(self.providers[self.primary_name], "client")

    def check_ready(self) -> None:
        self.providers[self.primary_name].check_ready()

    def _call(self, provider: LLMProvider, prompt: str, query: str) -> str:
        started = time.perf_counter()
        outcome = "ok"
        try:
            with timed(f"llm.provider.{provider.name}"):
                answer = provider.generate(prompt, query)
            if not answer:
                raise RuntimeError("Provider returned an empty answer")
            return answer
        except Exception:
            outcome = "error"
            raise
        finally:
            logger.info(json.dumps({
                "event": "llm_provider_call",
                "provider": provider.name,
                "outcome": outcome,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            }))

    def generate(self, prompt: str, query: str) -> str:
        primary = self.providers[self.primary_name]
        fallback = self.providers[self.fallback_name]
        logger.info(json.dumps({"event": "llm_provider_selected", "provider": primary.name}))
        try:
            return self._call(primary, prompt, query)
        except Exception as error:
            if primary.name == "gemini" and _is_transient(error):
                logger.warning(json.dumps({
                    "event": "llm_provider_retry", "provider": "gemini",
                    "reason": "transient_error",
                }))
                time.sleep(settings.EXTERNAL_RETRY_BASE_SECONDS)
                try:
                    return self._call(primary, prompt, query)
                except Exception as retry_error:
                    error = retry_error
            logger.warning(json.dumps({
                "event": "llm_fallback_activated", "from_provider": primary.name,
                "to_provider": fallback.name, "reason": type(error).__name__,
            }))
            try:
                return self._call(fallback, prompt, query)
            except Exception as fallback_error:
                logger.error(json.dumps({
                    "event": "llm_all_providers_failed", "providers": [primary.name, fallback.name],
                    "primary_error": type(error).__name__, "fallback_error": type(fallback_error).__name__,
                }))
                return UNAVAILABLE_ANSWER


llm_service = LLMService()
