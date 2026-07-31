"""Ingest one company-policy fact per markdown bullet or non-empty line."""

import argparse
from pathlib import Path

from app.models import MemoryScope
from app.services.memory import memory_service


def policy_facts(path: Path) -> list[str]:
    """Extract plain policy facts from a markdown file."""
    facts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip().lstrip("-*").strip()
        if text and not text.startswith("#"):
            facts.append(text)
    return facts


def ingest(path: Path) -> int:
    """Store all policy facts as global COMPANY memories."""
    for fact in policy_facts(path):
        memory_service.store_fact(
            content=fact,
            owner_id=None,
            scope=MemoryScope.COMPANY,
            source="company_policy_ingestion",
        )
    return len(policy_facts(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data/company_policies.md")
    args = parser.parse_args()
    print(f"Ingested {ingest(Path(args.path))} company policy facts.")
