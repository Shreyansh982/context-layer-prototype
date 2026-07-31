"""Minimal JSON logging and Prometheus-compatible in-process metrics."""

import json
import logging
import time
from collections import Counter
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.config import settings
from app.performance import finish_timing, start_timing, timed

logger = logging.getLogger("context_layer.requests")
request_count: Counter[tuple[str, int]] = Counter()
request_latency_seconds: Counter[str] = Counter()
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or __import__("uuid").uuid4().hex
        started = time.perf_counter()
        token = request_id_context.set(request_id)
        timing_token = start_timing("api", request_id, origin_request_id=request_id)
        outcome = "ok"
        response: Response | None = None
        try:
            response = await call_next(request)
        except Exception:
            outcome = "error"
            elapsed = time.perf_counter() - started
            request_count[(request.url.path, 500)] += 1
            request_latency_seconds[request.url.path] += elapsed
            logger.exception(json.dumps({"request_id": request_id, "path": request.url.path, "status": 500, "latency_ms": round(elapsed * 1000, 1)}))
            raise
        finally:
            finish_timing(timing_token, outcome, method=request.method, path=request.url.path, status=(response.status_code if response else 500))
            request_id_context.reset(token)
        elapsed = time.perf_counter() - started
        request_count[(request.url.path, response.status_code)] += 1
        request_latency_seconds[request.url.path] += elapsed
        response.headers["X-Request-ID"] = request_id
        logger.info(json.dumps({"request_id": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "latency_ms": round(elapsed * 1000, 1)}))
        return response


class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        import asyncio
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in {"/health", "/ready", "/metrics"} or request.url.path.startswith("/dashboard"):
            return await call_next(request)
        import asyncio
        try:
            with timed("request.queue_wait"):
                await asyncio.wait_for(self.semaphore.acquire(), timeout=settings.REQUEST_QUEUE_TIMEOUT_SECONDS)
        except TimeoutError:
            return PlainTextResponse("Service is at its configured demo capacity. Please retry shortly.", status_code=503)
        try:
            return await call_next(request)
        finally:
            self.semaphore.release()


def metrics_response() -> PlainTextResponse:
    lines = ["# TYPE context_layer_requests_total counter"]
    for (path, status), count in sorted(request_count.items()):
        lines.append(f'context_layer_requests_total{{path="{path}",status="{status}"}} {count}')
    lines.append("# TYPE context_layer_request_latency_seconds_total counter")
    for path, latency in sorted(request_latency_seconds.items()):
        lines.append(f'context_layer_request_latency_seconds_total{{path="{path}"}} {latency:.6f}')
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
