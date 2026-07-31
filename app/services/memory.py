"""
Memory Service

Responsible for storing and retrieving user memories.

Every memory is stored in

1. Neo4j
2. ChromaDB
"""

from datetime import datetime
import hashlib
import logging
import re

from app.models import MemoryFact, MemoryScope, Observation
from app.storage.neo4j import neo4j
from app.services.observation_validation import observation_validator
from app.services.evidence import record_evidence
from app.services.promotion_policy import evaluate_shadow
from app.services.promotion_comparison import PROMOTED, WRITER_FAILED, record_promotion_comparison

logger = logging.getLogger(__name__)


def content_hash(content: str) -> str:
    """Return the stable identity used for deterministic memory deduplication."""
    normalized = re.sub(r"\s+", " ", content.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def memory_id(content: str, owner_id: str | None, scope: MemoryScope,
              agent_id: str | None = None) -> str:
    """Return the canonical ID shared by the graph and vector stores."""
    owner_key = owner_id if scope == MemoryScope.USER else scope.value
    if scope in (MemoryScope.USER, MemoryScope.AGENT):
        owner_key = f"{owner_key}:{agent_id}"
    return hashlib.sha256(f"{scope.value}:{owner_key}:{content_hash(content)}".encode("utf-8")).hexdigest()


class MemoryService:

    def __init__(self, collection=None, embedder=None, queue=None):
        self.collection = collection
        self.embedder = embedder
        self.queue = queue

    def _queue(self):
        if self.queue is None:
            from app.services.jobs import job_queue
            self.queue = job_queue
        return self.queue

    def _collection(self):
        """Load the vector store only when a memory operation needs it."""
        if self.collection is None:
            from app.storage.chroma import chroma
            self.collection = chroma.get_collection()
        return self.collection

    def _embed(self, content: str) -> list[float]:
        """Load the embedding model only when a memory operation needs it."""
        if self.embedder is None:
            from app.services.embedding import embedding_service
            self.embedder = embedding_service.embed
        return self.embedder(content)

    def _memory_exists(self, embedding: list[float], scope: MemoryScope,
                       owner_id: str | None, agent_id: str | None,
                       threshold: float = 0.10) -> str | None:
        """Return the canonical vector ID for a near-duplicate, if present."""
        results = self._collection().query(
            query_embeddings=[embedding],
            n_results=1,
            where=self._memory_partition(scope, owner_id, agent_id)
        )
        if not results["ids"] or not results["ids"][0]:
            return None
        if results["distances"][0][0] <= threshold:
            return results["ids"][0][0]
        return None

    def _record_near_duplicate_observation(self, memory_id: str, owner_id: str | None,
                                           scope: MemoryScope, agent_id: str | None) -> None:
        """Increment the existing graph memory selected by Chroma similarity."""
        with neo4j.get_session() as session:
            session.run(
                '''
                MATCH (m:Memory {id:$id})
                WHERE m.scope=$scope
                  AND ($scope = $company_scope
                       OR ($scope = $user_scope AND m.owner_id=$owner_id AND m.agent_id=$agent_id)
                       OR ($scope = $agent_scope AND m.agent_id=$agent_id))
                SET m.times_observed=coalesce(m.times_observed, 1) + 1,
                    m.last_seen=$timestamp
                ''',
                id=memory_id,
                owner_id=owner_id,
                scope=scope.value,
                user_scope=MemoryScope.USER.value,
                agent_scope=MemoryScope.AGENT.value,
                company_scope=MemoryScope.COMPANY.value,
                agent_id=agent_id,
                timestamp=datetime.now().isoformat(),
            )

    def _store_user_memory(self, memory: MemoryFact, stable_hash: str) -> None:
        """Upsert a user memory and record each observation."""
        with neo4j.get_session() as session:
            session.run(
                '''
                MERGE (u:User {id:$owner_id})
                MERGE (m:Memory {content_hash:$content_hash, owner_id:$owner_id,
                    agent_id:$agent_id})
                ON CREATE SET m.id=$id, m.content=$content, m.scope=$scope,
                    m.confidence=$confidence, m.source=$source,
                    m.knowledge_reference=$knowledge_reference, m.timestamp=$timestamp,
                    m.times_observed=1, m.last_seen=$timestamp, m.vector_sync_status='PENDING'
                ON MATCH SET m.times_observed=coalesce(m.times_observed, 1) + 1,
                    m.last_seen=$timestamp, m.vector_sync_status='PENDING'
                MERGE (u)-[:HAS_MEMORY]->(m)
                ''',
                owner_id=memory.owner_id,
                agent_id=memory.agent_id,
                content_hash=stable_hash,
                id=memory.id,
                content=memory.content,
                scope=memory.scope.value,
                confidence=memory.confidence,
                source=memory.source,
                knowledge_reference=memory.knowledge_reference,
                timestamp=memory.timestamp.isoformat(),
            )

    def _store_company_memory(self, memory: MemoryFact, stable_hash: str) -> None:
        """Upsert global COMPANY memory without user or agent ownership."""
        with neo4j.get_session() as session:
            session.run(
                '''
                MERGE (m:Memory {content_hash:$content_hash, scope:$scope})
                ON CREATE SET m.id=$id, m.content=$content, m.confidence=$confidence,
                    m.source=$source, m.timestamp=$timestamp, m.times_observed=1,
                    m.last_seen=$timestamp, m.vector_sync_status='PENDING'
                ON MATCH SET m.times_observed=coalesce(m.times_observed, 1) + 1,
                    m.last_seen=$timestamp, m.vector_sync_status='PENDING'
                ''',
                content_hash=stable_hash, id=memory.id, content=memory.content,
                scope=memory.scope.value, confidence=memory.confidence,
                source=memory.source, timestamp=memory.timestamp.isoformat(),
            )

    def _store_agent_memory(self, memory: MemoryFact, stable_hash: str) -> None:
        """Upsert private operational memory keyed only by agent ID."""
        with neo4j.get_session() as session:
            session.run(
                '''
                MERGE (m:Memory {content_hash:$content_hash, scope:$scope, agent_id:$agent_id})
                ON CREATE SET m.id=$id, m.content=$content, m.confidence=$confidence,
                    m.source=$source, m.timestamp=$timestamp, m.times_observed=1,
                    m.last_seen=$timestamp, m.vector_sync_status='PENDING'
                ON MATCH SET m.times_observed=coalesce(m.times_observed, 1) + 1,
                    m.last_seen=$timestamp, m.vector_sync_status='PENDING'
                ''',
                content_hash=stable_hash, scope=memory.scope.value, agent_id=memory.agent_id,
                id=memory.id, content=memory.content, confidence=memory.confidence,
                source=memory.source, timestamp=memory.timestamp.isoformat(),
            )

    def _storage_owner(self, owner_id: str | None, scope: MemoryScope) -> str:
        """Use a stable Chroma partition for memories without a user owner."""
        return owner_id if scope == MemoryScope.USER else f"__{scope.value.lower()}__"

    def _memory_partition(self, scope: MemoryScope, owner_id: str | None,
                          agent_id: str | None) -> dict:
        """Build the Chroma metadata partition for a memory scope."""
        if scope == MemoryScope.USER:
            return {"$and": [{"user_id": owner_id}, {"agent_id": agent_id}]}
        if scope == MemoryScope.AGENT:
            return {"$and": [{"scope": scope.value}, {"agent_id": agent_id}]}
        return {"scope": scope.value}

    def store_fact(self, content: str, owner_id: str | None,
                   scope: MemoryScope = MemoryScope.USER,
                   confidence: float = 1.0,
                   source: str = "conversation",
                   knowledge_reference: str | None = None,
                   agent_id: str | None = None,
                   observation: Observation | None = None) -> MemoryFact | None:

        if scope == MemoryScope.USER and (not owner_id or not agent_id):
            raise ValueError("USER memory requires owner_id and agent_id")
        if scope == MemoryScope.AGENT and not agent_id:
            raise ValueError("AGENT memory requires agent_id")

        # Legacy sentence-memory callers now receive the same immutable input
        # contract as canonical writers.  It is intentionally not persisted in
        # Phase 2 so their current graph and promotion behaviour is unchanged.
        observation = observation or Observation.create(
            source=source,
            content=content,
            owner_id=owner_id,
            agent_id=agent_id,
            source_record_key=knowledge_reference,
            metadata={"scope": scope.value},
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        record_evidence(
            validated_observation,
            confidence=confidence,
            provenance={"writer": "legacy_memory", "scope": scope.value},
        )
        decision = evaluate_shadow(validated_observation)

        memory = MemoryFact(
            id=memory_id(content, owner_id, scope, agent_id),
            content=content,
            scope=scope,
            owner_id=owner_id,
            agent_id=agent_id,
            confidence=confidence,
            timestamp=datetime.now(),
            source=source,
            knowledge_reference=knowledge_reference,
        )

        stable_hash = content_hash(content)
        try:
            if scope == MemoryScope.USER:
                self._store_user_memory(memory, stable_hash)
            elif scope == MemoryScope.AGENT:
                self._store_agent_memory(memory, stable_hash)
            else:
                self._store_company_memory(memory, stable_hash)
        except Exception as error:
            record_promotion_comparison(validated_observation, decision, "legacy_memory", WRITER_FAILED, error)
            raise
        record_promotion_comparison(validated_observation, decision, "legacy_memory", PROMOTED)

        # Neo4j is authoritative.  Chroma can lag temporarily and is repaired
        # by the dedicated worker instead of deleting a valid graph record.
        self._queue().enqueue("vector-projection", {
            "memory_id": memory.id,
            "operation": "upsert",
            "origin_observation_id": observation.observation_id,
        })
        return memory

    def _upsert_vector(self, **kwargs) -> None:
        """Keep a single vector document for the canonical memory identity."""
        collection = self._collection()
        if hasattr(collection, "upsert"):
            collection.upsert(**kwargs)
        else:
            collection.add(**kwargs)

    def refresh_knowledge_memory(
        self,
        memory: MemoryFact,
        content: str,
        knowledge_reference: str,
        observation: Observation | None = None,
    ) -> MemoryFact:
        """Update a verified workbook memory only after its source row changes."""
        observation = observation or Observation.create(
            source="knowledge_memory_refresh",
            content=content,
            owner_id=memory.owner_id,
            agent_id=memory.agent_id,
            source_record_key=knowledge_reference,
            metadata={"memory_id": memory.id},
        )
        validated_observation = observation_validator.validate(observation)
        observation = validated_observation.observation
        record_evidence(
            validated_observation,
            confidence=memory.confidence,
            provenance={"writer": "knowledge_memory_refresh", "memory_id": memory.id},
        )
        decision = evaluate_shadow(validated_observation)
        try:
            with neo4j.get_session() as session:
                session.run(
                    '''
                    MATCH (:User {id:$owner_id})-[:HAS_MEMORY]->(m:Memory {id:$id, agent_id:$agent_id})
                    SET m.content=$content, m.knowledge_reference=$knowledge_reference,
                        m.last_seen=$timestamp, m.vector_sync_status='PENDING'
                    ''',
                    owner_id=memory.owner_id, agent_id=memory.agent_id, id=memory.id, content=content,
                    knowledge_reference=knowledge_reference,
                    timestamp=datetime.now().isoformat(),
                )
        except Exception as error:
            record_promotion_comparison(validated_observation, decision, "knowledge_memory_refresh", WRITER_FAILED, error)
            raise
        record_promotion_comparison(validated_observation, decision, "knowledge_memory_refresh", PROMOTED)
        self._queue().enqueue("vector-projection", {
            "memory_id": memory.id,
            "operation": "upsert",
            "origin_observation_id": observation.observation_id,
        })
        memory.content = content
        memory.knowledge_reference = knowledge_reference
        return memory

    def _memory_from_node(self, node) -> MemoryFact:
        return MemoryFact(
            id=node["id"], content=node["content"], scope=MemoryScope(node["scope"]),
            owner_id=node.get("owner_id"), agent_id=node.get("agent_id"),
            confidence=node["confidence"], timestamp=datetime.fromisoformat(node["timestamp"]),
            source=node.get("source", "conversation"),
            knowledge_reference=node.get("knowledge_reference"),
        )

    def get_memory_by_id(self, memory_id: str) -> MemoryFact | None:
        with neo4j.get_session() as session:
            record = session.run("MATCH (m:Memory {id:$id}) RETURN m", id=memory_id).single()
        return self._memory_from_node(record["m"]) if record else None

    def project_vector(self, memory_id: str, operation: str = "upsert") -> None:
        """Worker-only Chroma projection; safe to run repeatedly."""
        if operation == "delete":
            self._collection().delete(ids=[memory_id])
            return
        memory = self.get_memory_by_id(memory_id)
        if memory is None:
            self._collection().delete(ids=[memory_id])
            return
        embedding = self._embed(memory.content)
        self._upsert_vector(
            ids=[memory.id], documents=[memory.content], embeddings=[embedding],
            metadatas=[{
                "user_id": self._storage_owner(memory.owner_id, memory.scope),
                "scope": memory.scope.value, "confidence": memory.confidence,
                "source": memory.source, **({"agent_id": memory.agent_id} if memory.agent_id else {}),
            }],
        )
        with neo4j.get_session() as session:
            session.run("MATCH (m:Memory {id:$id}) SET m.vector_sync_status='SYNCED'", id=memory.id)

    def enqueue_pending_vector_projections(self) -> int:
        """Repair interrupted graph-to-vector projections after a restart."""
        with neo4j.get_session() as session:
            records = session.run(
                "MATCH (m:Memory) WHERE coalesce(m.vector_sync_status, 'PENDING') <> 'SYNCED' RETURN m.id AS id"
            )
            ids = [record["id"] for record in records]
        for memory_id in ids:
            self._queue().enqueue("vector-projection", {"memory_id": memory_id, "operation": "upsert"})
        return len(ids)

    def search(self, query: str, owner_id: str, agent_id: str, top_k: int) -> list[MemoryFact]:
        embedding = self._embed(query)

        # Chroma rejects requests for more results than it currently holds.
        # Avoid treating a new user's empty memory store as an API error.
        collection = self._collection()
        count = collection.count()
        if count == 0:
            return []

        results = collection.query(
            query_embeddings=[embedding],
            n_results=min(top_k, count),
            where={"$or": [
                {"$and": [{"user_id": owner_id}, {"agent_id": agent_id}]},
                {"scope": MemoryScope.COMPANY.value},
            ]},
        )

        logger.info(
            "search(): query=%r top_k=%d collection_count=%d returned=%d",
            query, top_k, count,
            len(results["ids"][0]) if results["ids"] else 0,
        )
        if results.get("ids") and results["ids"][0]:
            for doc, dist in zip(results.get("documents", [[]])[0], results.get("distances", [[]])[0]):
                logger.info("  candidate: distance=%.4f content=%r", dist, doc)

        memories = []

        if not results["ids"] or not results["ids"][0]:
            return memories

        with neo4j.get_session() as session:
            for memory_id in results["ids"][0]:
                record = session.run(
                    '''
                    MATCH (m:Memory {id:$id})
                    WHERE m.scope=$company_scope
                       OR (m.owner_id=$owner_id AND m.agent_id=$agent_id)
                    RETURN m
                    ''',
                    id=memory_id,
                    owner_id=owner_id,
                    agent_id=agent_id,
                    company_scope=MemoryScope.COMPANY.value,
                ).single()

                if record:
                    node = record["m"]
                    memories.append(self._memory_from_node(node))

        return memories

    def get_company_memory(self) -> list[MemoryFact]:
        """Return COMPANY facts for every user, independent of vector similarity."""
        memories = []
        with neo4j.get_session() as session:
            records = session.run(
                '''
                MATCH (m:Memory {scope:$scope})
                RETURN m
                ORDER BY m.timestamp
                ''',
                scope=MemoryScope.COMPANY.value,
            )
            for record in records:
                node = record["m"]
                memories.append(MemoryFact(
                    id=node["id"],
                    content=node["content"],
                    scope=MemoryScope.COMPANY,
                    owner_id=None,
                    agent_id=None,
                    confidence=node["confidence"],
                    timestamp=datetime.fromisoformat(node["timestamp"]),
                    source=node.get("source", "company_policy_ingestion"),
                    knowledge_reference=node.get("knowledge_reference"),
                ))
        return memories

    def get_user_memory(self, owner_id: str, agent_id: str) -> list[MemoryFact]:
        memories = []

        with neo4j.get_session() as session:
            results = session.run(
                '''
                MATCH (:User {id:$user_id})-[:HAS_MEMORY]->(m:Memory {agent_id:$agent_id})
                RETURN m
                ORDER BY m.timestamp
                ''',
                user_id=owner_id,
                agent_id=agent_id,
            )

            for record in results:
                node = record["m"]
                memories.append(
                    MemoryFact(
                        id=node["id"],
                        content=node["content"],
                        scope=MemoryScope(node["scope"]),
                        owner_id=owner_id,
                        agent_id=agent_id,
                        confidence=node["confidence"],
                        timestamp=datetime.fromisoformat(node["timestamp"]),
                        source=node.get("source", "conversation"),
                        knowledge_reference=node.get("knowledge_reference"),
                    )
                )

        return memories

    def get_agent_memory(self, agent_id: str) -> list[MemoryFact]:
        """Return private operational memory for exactly one agent."""
        memories = []
        with neo4j.get_session() as session:
            records = session.run(
                '''
                MATCH (m:Memory {scope:$scope, agent_id:$agent_id})
                RETURN m
                ORDER BY m.timestamp
                ''',
                scope=MemoryScope.AGENT.value,
                agent_id=agent_id,
            )
            for record in records:
                node = record["m"]
                memories.append(MemoryFact(
                    id=node["id"], content=node["content"], scope=MemoryScope.AGENT,
                    owner_id=None, agent_id=agent_id, confidence=node["confidence"],
                    timestamp=datetime.fromisoformat(node["timestamp"]),
                    source=node.get("source", "agent_state"),
                    knowledge_reference=node.get("knowledge_reference"),
                ))
        return memories

    def delete_user_memory(self, owner_id: str, agent_id: str) -> None:
        with neo4j.get_session() as session:
            ids = session.run(
                '''
                MATCH (:User {id:$user_id})-[:HAS_MEMORY]->(m:Memory {agent_id:$agent_id})
                RETURN m.id AS id
                ''',
                user_id=owner_id,
                agent_id=agent_id,
            )
            memory_ids = [r["id"] for r in ids]

            session.run(
                '''
                MATCH (:User {id:$user_id})-[r:HAS_MEMORY]->(m:Memory {agent_id:$agent_id})
                DELETE r,m
                ''',
                user_id=owner_id,
                agent_id=agent_id,
            )

            if memory_ids:
                for memory_id in memory_ids:
                    self._queue().enqueue("vector-projection", {"memory_id": memory_id, "operation": "delete"})

memory_service = MemoryService()
