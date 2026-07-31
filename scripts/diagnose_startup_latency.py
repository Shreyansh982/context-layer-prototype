"""Controlled cold/warm startup diagnostic; it does not mutate graph data."""

from __future__ import annotations

import argparse
import json

from app.config import settings
from app.performance import finish_timing, flush_performance_reports, start_timing, timed
from app.storage.neo4j import Neo4jConnection


def run(include_enterprise_cache: bool) -> dict | None:
    token = start_timing("worker", "startup-latency-diagnostic")
    outcome = "ok"
    probe = Neo4jConnection()  # A deliberately fresh Bolt pool for the cold probe.
    try:
        probe.diagnose_connectivity()
        with probe.get_session() as session:
            with timed("diagnostic.neo4j_cold_query"):
                session.run("RETURN 1 AS value").consume()
            with timed("diagnostic.neo4j_warm_query"):
                session.run("RETURN 1 AS value").consume()
        if include_enterprise_cache and settings.ENTERPRISE_ON_DEMAND_CACHE_ENABLED:
            from app.services.enterprise_cache import enterprise_profile_cache_service
            with timed("diagnostic.enterprise_cache_refresh"):
                enterprise_profile_cache_service.refresh_all()
        else:
            with timed("diagnostic.enterprise_cache_refresh_skipped"):
                pass
    except Exception:
        outcome = "error"
        raise
    finally:
        probe.close()
        summary = finish_timing(
            token, outcome,
            enterprise_cache_enabled=settings.ENTERPRISE_ON_DEMAND_CACHE_ENABLED,
            enterprise_cache_requested=include_enterprise_cache,
        )
        flush_performance_reports()
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-enterprise-cache", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.include_enterprise_cache), indent=2, sort_keys=True))
