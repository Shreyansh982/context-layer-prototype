from datetime import datetime

from app.models import ConversationMessage
from app.services.conversation import RedisConversationStore


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.values = {}

    def eval(self, _script, _keys, key, raw, buffer_size, _ttl):
        values = self.lists.setdefault(key, [])
        evicted = values.pop(0) if len(values) >= int(buffer_size) else ""
        values.append(raw)
        return evicted

    def lrange(self, key, _start, _end):
        return self.lists.get(key, [])

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.lists.pop(key, None)
            self.values.pop(key, None)


def message(content):
    return ConversationMessage(role="user", content=content, timestamp=datetime.now())


def test_redis_store_shares_fifo_window_between_service_instances():
    redis_client = FakeRedis()
    first = RedisConversationStore(redis_client)
    second = RedisConversationStore(redis_client)

    first.append("E0001", message("one"), buffer_size=2)
    first.append("E0001", message("two"), buffer_size=2)
    evicted = first.append("E0001", message("three"), buffer_size=2)

    assert evicted.content == "one"
    assert [item.content for item in second.get_messages("E0001")] == ["two", "three"]
    assert first.get_or_create_session_id("E0001") == second.get_or_create_session_id("E0001")
