"""Structured, privacy-aware diagnostics for deterministic context routing."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.config import settings
from app.observability import request_id_context
from app.extraction_trace import enabled as extraction_trace_enabled

logger = logging.getLogger("context_layer.routing")


def content_details(value: str | None) -> dict[str, Any]:
    """Expose full content only during deliberate local debugging."""
    text = value or ""
    details: dict[str, Any] = {
        "length": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    if settings.DEBUG_ROUTING_CONTENT:
        details["content"] = text
    return details


def log_routing_event(stage: str, **fields: Any) -> None:
    """Emit one JSON log record that can be correlated by external request ID."""
    payload = {"request_id": request_id_context.get(), "stage": stage, **fields}
    if extraction_trace_enabled():
        payload["trace_id"] = request_id_context.get()
    logger.info(json.dumps(payload, default=str, sort_keys=True))
