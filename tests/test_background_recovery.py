from contextlib import contextmanager
import json

from app.services.jobs import RedisJobQueue
from app.services.memory import MemoryService


class QueueSpy:
    def __init__(self):
        self.jobs = []

    def enqueue(self, stream, payload):
        self.jobs.append((stream, payload))


class PendingSession:
    def run(self, _query, **_params):
        return [{"id": "memory-1"}, {"id": "memory-2"}]


class PendingNeo4j:
    @contextmanager
    def get_session(self):
        yield PendingSession()


def test_restart_requeues_pending_vector_projections(monkeypatch):
    queue = QueueSpy()
    service = MemoryService(queue=queue)
    monkeypatch.setattr("app.services.memory.neo4j", PendingNeo4j())

    assert service.enqueue_pending_vector_projections() == 2
    assert queue.jobs == [
        ("vector-projection", {"memory_id": "memory-1", "operation": "upsert"}),
        ("vector-projection", {"memory_id": "memory-2", "operation": "upsert"}),
    ]


class JobRedisSpy:
    def __init__(self):
        self.added = []
        self.acked = []

    def xadd(self, stream, fields):
        self.added.append((stream, fields))

    def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))


def test_failed_job_is_requeued_with_a_bounded_attempt(monkeypatch):
    client = JobRedisSpy()
    queue = RedisJobQueue(client)
    monkeypatch.setattr("app.services.jobs.time.sleep", lambda _seconds: None)

    queue._process("memory-extraction", "workers", "1-0", {"payload": json.dumps({"id": 1}), "attempt": "0"}, lambda _payload: (_ for _ in ()).throw(RuntimeError("temporary")))

    assert client.added == [("memory-extraction", {"payload": '{"id": 1}', "attempt": "1"})]
    assert client.acked == [("memory-extraction", "workers", "1-0")]
