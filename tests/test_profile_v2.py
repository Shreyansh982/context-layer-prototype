from app.services.predicate_registry import PredicateRegistry, predicate_registry
from app.services.profile import DeterministicProfileParser, ProfileNoteFilter, ProfileObservationStore, ProfileSignal


class _Pipeline:
    def __init__(self, client):
        self.client = client
        self.operations = []

    def zremrangebyscore(self, *args):
        self.operations.append(("trim", args)); return self

    def zadd(self, *args):
        self.operations.append(("add", args)); return self

    def expire(self, *args):
        return self

    def zrange(self, *args):
        self.operations.append(("range", args)); return self

    def execute(self):
        result = []
        for operation, args in self.operations:
            if operation == "trim":
                result.append(0)
            elif operation == "add":
                key, values = args
                self.client.values.setdefault(key, {}).update(values)
                result.append(1)
            elif operation == "range":
                result.append(sorted(self.client.values.get(args[0], {})))
        return result


class _Redis:
    def __init__(self):
        self.ids = set()
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.ids:
            return False
        self.ids.add(key)
        return True

    def pipeline(self, transaction=True):
        return _Pipeline(self)


def test_python_registry_is_used_through_interface():
    assert isinstance(predicate_registry, PredicateRegistry)
    assert predicate_registry.resolve("manager is").name == "REPORTS_TO"
    assert predicate_registry.definition("PREF_RESPONSE_LENGTH").profile_dimension == "response_length"


def test_profile_parser_distinguishes_explicit_and_temporary_requests():
    parser = DeterministicProfileParser()
    assert parser.parse("Remember that I always prefer concise answers.") == [
        ProfileSignal("PREF_RESPONSE_LENGTH", "CONCISE", True)
    ]
    assert parser.parse("Use bullet points for this response.") == []


def test_profile_note_filter_accepts_only_declarative_self_information():
    notes = ProfileNoteFilter()
    assert notes.accepts("I enjoy learning distributed systems.")
    assert notes.accepts("Remember that my hobby is cycling.")
    assert not notes.accepts("Paris is the capital of France.")
    assert not notes.accepts("What is my hobby?")
    assert not notes.accepts("I need this answer in bullets for this response.")


def test_redis_observation_store_counts_one_signal_per_session_and_message():
    store = ProfileObservationStore(client=_Redis())
    signal = ProfileSignal("PREF_TONE", "PROFESSIONAL")
    assert store.record("E0001", "session-a", "message-1", signal) == ["session-a"]
    assert store.record("E0001", "session-a", "message-1", signal) is None
    assert store.record("E0001", "session-a", "message-2", signal) == ["session-a"]
    assert store.record("E0001", "session-b", "message-3", signal) == ["session-a", "session-b"]
