"""Local, privacy-safe performance diagnostics for API requests and workers."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


logger = logging.getLogger("context_layer.performance")
timing_context: ContextVar["TimingContext | None"] = ContextVar("timing_context", default=None)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) * percentile + 0.999999)) - 1))]


class TimingContext:
    def __init__(self, role: str, operation_id: str, origin_request_id: str | None = None):
        self.role = role
        self.operation_id = operation_id
        self.origin_request_id = origin_request_id
        self.started = time.perf_counter()
        self.stages: list[dict[str, Any]] = []

    @contextmanager
    def span(self, stage: str):
        started = time.perf_counter()
        outcome = "ok"
        try:
            yield
        except Exception:
            outcome = "error"
            raise
        finally:
            self.stages.append({"stage": stage, "duration_ms": round((time.perf_counter() - started) * 1000, 3), "outcome": outcome})

    def summary(self, outcome: str, **metadata: Any) -> dict[str, Any]:
        grouped: dict[str, dict[str, Any]] = {}
        for sample in self.stages:
            bucket = grouped.setdefault(sample["stage"], {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "error_count": 0})
            bucket["count"] += 1
            bucket["total_ms"] += sample["duration_ms"]
            bucket["max_ms"] = max(bucket["max_ms"], sample["duration_ms"])
            bucket["error_count"] += int(sample["outcome"] == "error")
        for bucket in grouped.values():
            bucket["total_ms"] = round(bucket["total_ms"], 3)
            bucket["max_ms"] = round(bucket["max_ms"], 3)
        total_ms = round((time.perf_counter() - self.started) * 1000, 3)
        grouped["request.total" if self.role == "api" else "worker.job.total"] = {
            "count": 1, "total_ms": total_ms, "max_ms": total_ms, "error_count": int(outcome == "error")
        }
        return {
            "event": "request_timing_summary" if self.role == "api" else "worker_timing_summary",
            "operation_id": self.operation_id,
            "origin_request_id": self.origin_request_id,
            "role": self.role,
            "outcome": outcome,
            "total_ms": total_ms,
            "stages": grouped,
            **metadata,
        }


class PerformanceReporter:
    """Bounded in-process samples, periodically written as JSON Lines."""

    def __init__(self, role: str):
        self.role = role
        self._events: deque[dict[str, Any]] = deque(maxlen=settings.PERFORMANCE_SAMPLE_LIMIT)
        self._lock = threading.Lock()
        self._window_started = datetime.now(timezone.utc)
        self._last_flush = time.monotonic()

    def record(self, summary: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(summary)
            if time.monotonic() - self._last_flush >= settings.PERFORMANCE_SUMMARY_INTERVAL_SECONDS:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._events:
            self._last_flush = time.monotonic()
            self._window_started = datetime.now(timezone.utc)
            return
        stage_samples: dict[str, list[float]] = defaultdict(list)
        stage_errors: dict[str, int] = defaultdict(int)
        slowest = max(self._events, key=lambda event: event["total_ms"])
        for event in self._events:
            for stage, aggregate in event["stages"].items():
                # Request summaries retain per-stage totals; workers normally
                # have one span per stage, and both remain useful for triage.
                stage_samples[stage].append(float(aggregate["total_ms"]))
                stage_errors[stage] += int(aggregate["error_count"])
        report = {
            "event": "performance_interval_summary",
            "role": self.role,
            "pid": os.getpid(),
            "window_started_at": self._window_started.isoformat(),
            "window_ended_at": datetime.now(timezone.utc).isoformat(),
            "events_retained": len(self._events),
            "slowest_operation_id": slowest["operation_id"],
            "slowest_total_ms": slowest["total_ms"],
            "stages": {
                stage: {
                    "count": len(values), "total_ms": round(sum(values), 3),
                    "min_ms": round(min(values), 3), "avg_ms": round(sum(values) / len(values), 3),
                    "p50_ms": round(_percentile(values, 0.50), 3), "p95_ms": round(_percentile(values, 0.95), 3),
                    "max_ms": round(max(values), 3), "error_count": stage_errors[stage],
                }
                for stage, values in stage_samples.items()
            },
        }
        directory = Path(settings.PERFORMANCE_SUMMARY_DIRECTORY)
        directory.mkdir(parents=True, exist_ok=True)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target = directory / f"{self.role}-performance-summary-{date}.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True) + "\n")
        self._remove_expired_reports(directory)
        logger.info(json.dumps(report, sort_keys=True))
        self._events.clear()
        self._last_flush = time.monotonic()
        self._window_started = datetime.now(timezone.utc)

    def _remove_expired_reports(self, directory: Path) -> None:
        cutoff = time.time() - settings.PERFORMANCE_REPORT_RETENTION_DAYS * 86_400
        for path in directory.glob(f"{self.role}-performance-summary-*.jsonl"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()


_reporters: dict[str, PerformanceReporter] = {}
_reporter_lock = threading.Lock()


def _reporter(role: str) -> PerformanceReporter:
    with _reporter_lock:
        return _reporters.setdefault(role, PerformanceReporter(role))


def start_timing(role: str, operation_id: str, origin_request_id: str | None = None):
    if not settings.PERFORMANCE_INSTRUMENTATION_ENABLED:
        return None
    return timing_context.set(TimingContext(role, operation_id, origin_request_id))


def finish_timing(token, outcome: str = "ok", **metadata: Any) -> dict[str, Any] | None:
    if token is None:
        return None
    context = timing_context.get()
    try:
        if context is None:
            return None
        summary = context.summary(outcome, **metadata)
        logger.info(json.dumps(summary, sort_keys=True))
        _reporter(context.role).record(summary)
        return summary
    finally:
        timing_context.reset(token)


@contextmanager
def timed(stage: str):
    context = timing_context.get()
    if context is None or not settings.PERFORMANCE_INSTRUMENTATION_ENABLED:
        yield
        return
    with context.span(stage):
        yield


def current_operation_id() -> str | None:
    context = timing_context.get()
    return context.operation_id if context else None


def flush_performance_reports() -> None:
    """Force an interval report; used only by the standalone diagnostic tool."""
    with _reporter_lock:
        reporters = list(_reporters.values())
    for reporter in reporters:
        reporter.flush()
