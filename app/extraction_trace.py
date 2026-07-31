"""Opt-in JSONL tracing for the asynchronous memory-extraction pipeline."""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import settings


logger = logging.getLogger("context_layer.extraction_trace")
trace_id_context: ContextVar[str | None] = ContextVar("extraction_trace_id", default=None)
_write_lock = threading.Lock()


def enabled() -> bool:
    return settings.EXTRACTION_TRACE


def new_trace_id() -> str:
    return str(uuid4())


def current_trace_id() -> str | None:
    return trace_id_context.get()


@contextmanager
def activate(trace_id: str | None):
    if not enabled() or not trace_id:
        yield
        return
    token = trace_id_context.set(trace_id)
    try:
        yield
    finally:
        trace_id_context.reset(token)


def emit(stage: str, **details: Any) -> None:
    """Append one self-contained event to ``logs/extraction/<trace_id>.jsonl``."""
    trace_id = current_trace_id()
    if not enabled() or not trace_id:
        return
    event = {
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        **details,
    }
    directory = Path(settings.EXTRACTION_TRACE_DIRECTORY)
    with _write_lock:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{trace_id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str, sort_keys=True) + "\n")
    logger.info("extraction_trace trace_id=%s stage=%s", trace_id, stage)


@contextmanager
def stage(name: str, **details: Any):
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except Exception as error:
        outcome = "error"
        details["error_type"] = type(error).__name__
        raise
    finally:
        emit(name, outcome=outcome, duration_ms=round((time.perf_counter() - started) * 1000, 3), **details)


def warning(code: str, message: str, **details: Any) -> None:
    emit("validation_warning", severity="warning", code=code, message=message, **details)
