"""Shared short-term conversation state with Neo4j as the durable archive."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from uuid import uuid4

import redis

from app.config import settings
from app.models import ConversationMessage
from app.resilience import retry_external
from app.storage.neo4j import neo4j
from app.performance import timed


class ConversationStore(ABC):
    """Fast shared store for active conversation windows."""

    @abstractmethod
    def append(self, user_id: str, message: ConversationMessage, buffer_size: int) -> ConversationMessage | None: ...

    @abstractmethod
    def get_messages(self, user_id: str) -> list[ConversationMessage]: ...

    @abstractmethod
    def get_or_create_session_id(self, user_id: str) -> str: ...

    @abstractmethod
    def clear(self, user_id: str) -> None: ...


class RedisConversationStore(ConversationStore):
    """Redis-backed FIFO buffer. The Lua script makes eviction and append atomic."""

    _append_script = """
    local evicted = ''
    if redis.call('LLEN', KEYS[1]) >= tonumber(ARGV[2]) then
      evicted = redis.call('LPOP', KEYS[1])
    end
    redis.call('RPUSH', KEYS[1], ARGV[1])
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return evicted
    """

    def __init__(self, client=None):
        self.client = client or redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

    @staticmethod
    def _conversation_key(user_id: str) -> str:
        return f"context-layer:conversation:{user_id}"

    @staticmethod
    def _session_key(user_id: str) -> str:
        return f"context-layer:session:{user_id}"

    @staticmethod
    def _dump(message: ConversationMessage) -> str:
        return message.model_dump_json()

    @staticmethod
    def _load(raw: str) -> ConversationMessage:
        return ConversationMessage.model_validate_json(raw)

    def append(self, user_id: str, message: ConversationMessage, buffer_size: int) -> ConversationMessage | None:
        def operation():
            evicted = self.client.eval(
                self._append_script, 1, self._conversation_key(user_id), self._dump(message),
                buffer_size, settings.CONVERSATION_TTL_SECONDS,
            )
            return self._load(evicted) if evicted else None
        with timed("redis.conversation_append"):
            return retry_external("Redis conversation store", operation)

    def get_messages(self, user_id: str) -> list[ConversationMessage]:
        with timed("redis.conversation_get"):
            return retry_external(
                "Redis conversation store",
                lambda: [self._load(raw) for raw in self.client.lrange(self._conversation_key(user_id), 0, -1)],
            )

    def get_or_create_session_id(self, user_id: str) -> str:
        key = self._session_key(user_id)
        def operation():
            existing = self.client.get(key)
            if existing:
                return existing
            session_id = str(uuid4())
            if self.client.set(key, session_id, nx=True, ex=settings.CONVERSATION_TTL_SECONDS):
                return session_id
            return self.client.get(key)
        with timed("redis.session"):
            return retry_external("Redis session store", operation)

    def clear(self, user_id: str) -> None:
        retry_external("Redis conversation store", lambda: self.client.delete(self._conversation_key(user_id), self._session_key(user_id)))


class ConversationService:
    def __init__(self, store: ConversationStore | None = None):
        self.store = store or RedisConversationStore()

    def _persist_message(self, user_id: str, session_id: str, message: ConversationMessage) -> None:
        with neo4j.get_session() as session:
            session.run(
                '''
                MERGE (u:User {id:$user_id})
                MERGE (s:Session {id:$session_id})
                ON CREATE SET s.created_at=$timestamp
                MERGE (u)-[:HAD_SESSION]->(s)
                CREATE (m:Message {id:$message_id, role:$role, content:$content,
                    timestamp:$timestamp})
                CREATE (s)-[:HAD_MESSAGE]->(m)
                ''',
                user_id=user_id, session_id=session_id, message_id=message.id, role=message.role,
                content=message.content, timestamp=message.timestamp.isoformat(),
            )

    def add_message_with_receipt(self, user_id: str, role: str, content: str) -> tuple[ConversationMessage, ConversationMessage | None]:
        """Archive a message and return it with the possible FIFO eviction."""
        session_id = self.store.get_or_create_session_id(user_id)
        message = ConversationMessage(role=role, content=content, timestamp=datetime.now(), session_id=session_id)
        # Archive first: a successful response must not be visible only in Redis.
        self._persist_message(user_id, session_id, message)
        return message, self.store.append(user_id, message, settings.BUFFER_SIZE)

    def add_message(self, user_id: str, role: str, content: str) -> ConversationMessage | None:
        """Compatibility wrapper returning only the evicted message."""
        _, evicted = self.add_message_with_receipt(user_id, role, content)
        return evicted

    def get_conversation(self, user_id: str) -> list[ConversationMessage]:
        return self.store.get_messages(user_id)

    def list_past_sessions(self, user_id: str) -> list[dict]:
        with neo4j.get_session() as session:
            records = session.run(
                '''
                MATCH (:User {id:$user_id})-[:HAD_SESSION]->(s:Session)
                OPTIONAL MATCH (s)-[:HAD_MESSAGE]->(m:Message)
                RETURN s.id AS session_id, s.created_at AS created_at,
                    m.role AS role, m.content AS content, m.timestamp AS timestamp
                ORDER BY s.created_at, m.timestamp
                ''', user_id=user_id,
            )
            sessions = {}
            for record in records:
                current = sessions.setdefault(record["session_id"], {"id": record["session_id"], "created_at": record["created_at"], "messages": []})
                if record["role"] is not None:
                    current["messages"].append({"role": record["role"], "content": record["content"], "timestamp": record["timestamp"]})
            return list(sessions.values())

    def get_extraction_window(self, user_id: str, evicted_message: ConversationMessage, window_size: int = 3) -> list[ConversationMessage]:
        return [evicted_message] + self.get_conversation(user_id)[:window_size]

    def clear_conversation(self, user_id: str) -> None:
        self.store.clear(user_id)


conversation_service = ConversationService()
