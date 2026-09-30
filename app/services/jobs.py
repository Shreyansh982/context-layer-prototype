"""Redis Stream work queues for extraction and vector projection."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

import redis

from app.config import settings
from app.resilience import retry_external
from app.performance import current_operation_id, finish_timing, start_timing, timed

logger = logging.getLogger(__name__)


class RedisJobQueue:
    """Small durable queue with consumer groups, retry and dead-letter streams."""

    def __init__(self, client=None):
        self.client = client or redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    def enqueue(self, stream: str, payload: dict, attempt: int = 0) -> str:
        payload = dict(payload)
        payload.setdefault("origin_request_id", current_operation_id())
        fields = {"payload": json.dumps(payload, default=str), "attempt": str(attempt)}
        with timed("redis.job_enqueue"):
            return retry_external("Redis job queue", lambda: self.client.xadd(stream, fields))

    _enqueue_once_script = """
    if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then
      return redis.call('XADD', KEYS[2], '*', 'payload', ARGV[2], 'attempt', ARGV[3])
    end
    return ''
    """

    def enqueue_once(
        self,
        stream: str,
        payload: dict,
        *,
        idempotency_key: str,
        ttl_seconds: int,
        attempt: int = 0,
    ) -> str | None:
        """Atomically enqueue a logical operation once during its TTL window.

        The Redis script claims the idempotency key and appends to the Stream
        together, so repeated API reads cannot reserve work without queuing it
        or produce duplicate deferred jobs.
        """
        payload = dict(payload)
        payload.setdefault("origin_request_id", current_operation_id())
        encoded = json.dumps(payload, default=str)
        with timed("redis.job_enqueue_once"):
            result = retry_external(
                "Redis job queue",
                lambda: self.client.eval(
                    self._enqueue_once_script,
                    2,
                    idempotency_key,
                    stream,
                    max(1, int(ttl_seconds)),
                    encoded,
                    str(attempt),
                ),
            )
        return str(result) if result else None

    def _ensure_group(self, stream: str, group: str) -> None:
        try:
            self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    def process_forever(self, stream: str, group: str, consumer: str, handler: Callable[[dict], None]) -> None:
        self._ensure_group(stream, group)
        while True:
            messages = self.client.xreadgroup(group, consumer, {stream: ">"}, count=1, block=1000)
            if not messages:
                self._claim_and_process(stream, group, consumer, handler)
                continue
            for _, entries in messages:
                for message_id, fields in entries:
                    self._process(stream, group, message_id, fields, handler)

    def _claim_and_process(self, stream: str, group: str, consumer: str, handler: Callable[[dict], None]) -> None:
        """Recover work left pending by a stopped worker after a short idle period."""
        try:
            _, entries, _ = self.client.xautoclaim(stream, group, consumer, min_idle_time=5_000, start_id="0-0", count=1)
        except (AttributeError, redis.exceptions.ResponseError):
            return
        for message_id, fields in entries:
            self._process(stream, group, message_id, fields, handler)

    def _process(self, stream: str, group: str, message_id: str, fields: dict, handler: Callable[[dict], None]) -> None:
        attempt = int(fields.get("attempt", "0"))
        payload = json.loads(fields["payload"])
        token = start_timing("worker", f"{stream}:{message_id}", payload.get("origin_request_id"))
        outcome = "ok"
        try:
            with timed(f"worker.{stream}"):
                handler(payload)
        except Exception:
            outcome = "error"
            logger.exception("Job failed: stream=%s id=%s attempt=%d", stream, message_id, attempt + 1)
            max_attempts = settings.PROFILE_JOB_MAX_ATTEMPTS if stream == "profile-observation" else settings.WORKER_MAX_ATTEMPTS
            if attempt + 1 >= max_attempts:
                self.client.xadd(f"{stream}:dead-letter", {"payload": fields["payload"], "error": "max attempts exceeded"})
                self.client.xack(stream, group, message_id)
                return
            # Keep the current item pending until the bounded backoff finishes;
            # a process crash leaves it recoverable through XAUTOCLAIM.
            time.sleep(min(2 ** attempt, 8))
            self.client.xadd(stream, {"payload": fields["payload"], "attempt": str(attempt + 1)})
            self.client.xack(stream, group, message_id)
            return
        finally:
            finish_timing(token, outcome, stream=stream, attempt=attempt)
        self.client.xack(stream, group, message_id)


job_queue = RedisJobQueue()
