CREATE CONSTRAINT user_id_unique IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE;
CREATE CONSTRAINT memory_id_unique IF NOT EXISTS FOR (m:Memory) REQUIRE m.id IS UNIQUE;
CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT message_id_unique IF NOT EXISTS FOR (m:Message) REQUIRE m.id IS UNIQUE;
CREATE INDEX memory_owner_agent IF NOT EXISTS FOR (m:Memory) ON (m.owner_id, m.agent_id);
CREATE INDEX memory_scope IF NOT EXISTS FOR (m:Memory) ON (m.scope);
