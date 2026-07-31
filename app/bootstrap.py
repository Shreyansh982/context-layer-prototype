"""Idempotent container bootstrap: schema then demo policy seed."""

from pathlib import Path

from app.models import MemoryScope
from app.config import settings
from app.services.memory import memory_service
from app.services.facts import canonical_fact_service
from app.storage.neo4j import neo4j


def run_migrations() -> None:
    migration_dir = Path(__file__).with_name("migrations")
    with neo4j.get_session() as session:
        for file in sorted(migration_dir.glob("*.cypher")):
            for statement in file.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    session.run(statement)


def seed_company_policies() -> None:
    policy_file = Path(__file__).resolve().parents[1] / "data" / "company_policies.md"
    for line in policy_file.read_text(encoding="utf-8").splitlines():
        fact = line.strip().lstrip("-* ").strip()
        if fact and not fact.startswith("#"):
            memory_service.store_fact(fact, owner_id=None, scope=MemoryScope.COMPANY, source="company_policy_ingestion")


def run_startup_tasks() -> None:
    """Apply schema and optional development seed data before serving traffic."""
    run_migrations()
    if settings.ENABLE_COMPANY_POLICY_SEED:
        seed_company_policies()
    memory_service.enqueue_pending_vector_projections()
    canonical_fact_service.enqueue_pending_vector_projections()


if __name__ == "__main__":
    run_startup_tasks()
