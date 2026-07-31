from contextlib import contextmanager

from app.models import MemoryScope
from app.services.memory import MemoryService, content_hash


class FakeCollection:
    def __init__(self):
        self.entries = {}

    def query(self, **kwargs):
        matching_ids = [
            memory_id for memory_id, entry in self.entries.items()
            if _matches_where(entry["metadata"], kwargs["where"])
        ]
        if not matching_ids:
            return {"ids": [[]], "distances": [[]]}
        memory_id = matching_ids[0]
        return {"ids": [[memory_id]], "distances": [[0.01]]}

    def add(self, **kwargs):
        self.entries[kwargs["ids"][0]] = {
            "document": kwargs["documents"][0],
            "metadata": kwargs["metadatas"][0],
        }

    def count(self):
        return len(self.entries)


class FakeQueue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, stream, payload):
        self.jobs.append((stream, payload))


def _matches_where(metadata, where):
    if "$and" in where:
        return all(_matches_where(metadata, item) for item in where["$and"])
    if "$or" in where:
        return any(_matches_where(metadata, item) for item in where["$or"])
    return all(metadata.get(key) == value for key, value in where.items())


class FakeSession:
    def __init__(self, graph):
        self.graph = graph

    def run(self, query, **params):
        if "MATCH (m:Memory {id:$id})" in query and "WHERE m.scope=$company_scope" in query:
            matches = [
                {"m": node} for node in self.graph.values()
                if node.get("id") == params["id"]
                and (
                    node.get("scope") == params["company_scope"]
                    or (node.get("owner_id") == params["owner_id"] and node.get("agent_id") == params["agent_id"])
                )
            ]
            return FakeResult(matches)
        if "RETURN m" in query and "MATCH (:User" in query:
            return FakeResult([
                {"m": node} for node in self.graph.values()
                if node.get("owner_id") == params["user_id"]
                and node.get("agent_id") == params["agent_id"]
            ])
        if "RETURN m" in query and "scope:$scope, agent_id:$agent_id" in query:
            return FakeResult([
                {"m": node} for node in self.graph.values()
                if node.get("scope") == params["scope"]
                and node.get("agent_id") == params["agent_id"]
            ])
        if "RETURN m" in query and "MATCH (m:Memory {scope:$scope})" in query:
            return FakeResult([
                {"m": node} for node in self.graph.values()
                if node.get("scope") == params["scope"]
            ])
        if "MERGE (m:Memory" in query:
            key = (params["content_hash"], params.get("owner_id"), params.get("agent_id"), params.get("scope"))
            node = self.graph.get(key)
            if node is None:
                self.graph[key] = {"times_observed": 1, **params}
            else:
                node["times_observed"] += 1
            return []
        if "SET m.times_observed" in query:
            for node in self.graph.values():
                if node["id"] == params["id"]:
                    node["times_observed"] += 1
            return []
        return []


class FakeResult(list):
    def single(self):
        return self[0] if self else None


class FakeNeo4j:
    def __init__(self):
        self.graph = {}

    @contextmanager
    def get_session(self):
        yield FakeSession(self.graph)


def test_store_same_fact_twice_creates_one_memory_and_counts_observations(monkeypatch):
    fake_neo4j = FakeNeo4j()
    service = MemoryService(collection=FakeCollection(), embedder=lambda _content: [0.1], queue=FakeQueue())
    monkeypatch.setattr("app.services.memory.neo4j", fake_neo4j)

    service.store_fact("  User   prefers Python ", "E0001", MemoryScope.USER, agent_id="agent-a")
    service.store_fact("user prefers python", "E0001", MemoryScope.USER, agent_id="agent-a")

    assert len(fake_neo4j.graph) == 1
    node = next(iter(fake_neo4j.graph.values()))
    assert node["times_observed"] == 2


def test_distinct_facts_are_durably_stored_before_async_vector_projection(monkeypatch):
    fake_neo4j = FakeNeo4j()
    queue = FakeQueue()
    service = MemoryService(collection=FakeCollection(), embedder=lambda _content: [0.1], queue=queue)
    monkeypatch.setattr("app.services.memory.neo4j", fake_neo4j)

    service.store_fact("User's ID is E0005", "E0005", MemoryScope.USER, agent_id="agent-a")
    service.store_fact("USER_ID: E0005", "E0005", MemoryScope.USER, agent_id="agent-a")

    assert len(fake_neo4j.graph) == 2
    assert len(queue.jobs) == 2


def test_user_and_agent_scopes_are_partitioned_by_agent_id(monkeypatch):
    fake_neo4j = FakeNeo4j()
    service = MemoryService(collection=FakeCollection(), embedder=lambda _content: [0.1], queue=FakeQueue())
    monkeypatch.setattr("app.services.memory.neo4j", fake_neo4j)

    service.store_fact("User works in IT.", "E0005", MemoryScope.USER, agent_id="agent-a")
    service.store_fact("Extraction failed once.", None, MemoryScope.AGENT, agent_id="agent-a")
    service.store_fact("Company leave policy.", None, MemoryScope.COMPANY)

    assert service.get_user_memory("E0005", "agent-b") == []
    assert [fact.content for fact in service.get_user_memory("E0005", "agent-a")] == ["User works in IT."]
    # The Chroma document is created asynchronously by the vector worker; the
    # graph remains immediately authoritative for deterministic retrieval.
    assert service.search("work", "E0005", "agent-b", top_k=5) == []
    assert service.search("work", "E0005", "agent-a", top_k=5) == []
    assert [fact.content for fact in service.get_agent_memory("agent-a")] == ["Extraction failed once."]
    assert service.get_agent_memory("agent-b") == []
    assert [fact.content for fact in service.get_company_memory()] == ["Company leave policy."]
