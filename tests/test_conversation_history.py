from contextlib import contextmanager

from app.services.conversation import ConversationService


class FakeConversationStore:
    def __init__(self):
        self.messages = {}
        self.sessions = {}

    def append(self, user_id, message, buffer_size):
        buffer = self.messages.setdefault(user_id, [])
        evicted = buffer.pop(0) if len(buffer) >= buffer_size else None
        buffer.append(message)
        return evicted

    def get_messages(self, user_id):
        return self.messages.get(user_id, [])

    def get_or_create_session_id(self, user_id):
        return self.sessions.setdefault(user_id, "session-1")

    def clear(self, user_id):
        self.messages.pop(user_id, None)


class FakeSession:
    def __init__(self):
        self.rows = []

    def run(self, query, **params):
        if "CREATE (m:Message" in query:
            self.rows.append({
                "session_id": params["session_id"],
                "created_at": params["timestamp"],
                "role": params["role"],
                "content": params["content"],
                "timestamp": params["timestamp"],
            })
            return []
        return self.rows


class FakeNeo4j:
    def __init__(self):
        self.session = FakeSession()

    @contextmanager
    def get_session(self):
        yield self.session


def test_writes_and_lists_persistent_session_history(monkeypatch):
    fake_neo4j = FakeNeo4j()
    monkeypatch.setattr("app.services.conversation.neo4j", fake_neo4j)
    service = ConversationService(store=FakeConversationStore())

    service.add_message("E0001", "user", "Hello")

    sessions = service.list_past_sessions("E0001")
    assert len(sessions) == 1
    assert sessions[0]["messages"] == [{
        "role": "user", "content": "Hello", "timestamp": sessions[0]["messages"][0]["timestamp"],
    }]
