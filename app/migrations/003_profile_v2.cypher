// Additive indexes for Version 2 profile and on-demand enterprise cache reads.
CREATE INDEX fact_profile_dimension_current IF NOT EXISTS FOR (f:Fact) ON (f.owner_id, f.profile_dimension, f.current);
CREATE INDEX fact_enterprise_cache_key IF NOT EXISTS FOR (f:Fact) ON (f.cache_key, f.current);
CREATE INDEX fact_enterprise_cache_source IF NOT EXISTS FOR (f:Fact) ON (f.source_system, f.current);
