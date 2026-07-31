CREATE CONSTRAINT entity_key_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE;
CREATE CONSTRAINT fact_key_unique IF NOT EXISTS FOR (f:Fact) REQUIRE f.fact_key IS UNIQUE;
CREATE CONSTRAINT fact_observation_key_unique IF NOT EXISTS FOR (o:FactObservation) REQUIRE o.observation_key IS UNIQUE;
CREATE CONSTRAINT enterprise_snapshot_id_unique IF NOT EXISTS FOR (s:EnterpriseSnapshot) REQUIRE s.snapshot_id IS UNIQUE;
CREATE INDEX fact_owner_current IF NOT EXISTS FOR (f:Fact) ON (f.owner_id, f.current);
CREATE INDEX fact_source_snapshot IF NOT EXISTS FOR (f:Fact) ON (f.source_system, f.last_snapshot);
