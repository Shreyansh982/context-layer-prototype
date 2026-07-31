"""Cut over passive evidence aggregation to canonical fact keys.

Usage:
    python scripts/migrate_evidence_aggregates.py [--apply]

Version 1 aggregates used raw extractor subject/predicate/object hashes.  The
redacted observation ledger intentionally lacks enough canonical entity type
and qualifier information to backfill those raw rows safely.  This migration
is therefore a non-destructive v2 cutover: legacy aggregates stay available
until TTL expiry, while new/replayed canonical writes create fact-key
aggregates that are the only aggregates used by canonical policy evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import redis

# Support the documented ``python scripts/...`` invocation as well as
# ``python -m scripts...`` from a container whose working directory differs.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


MIGRATION_KEY = "context-layer:evidence:aggregate-schema-version"
TARGET_VERSION = "2"


def _aggregate_count(client, pattern: str) -> int:
    derived_suffixes = (":sessions", ":sources")
    return sum(
        1
        for key in client.scan_iter(match=pattern)
        if not key.endswith(derived_suffixes) and ":observation:" not in key
    )


def _canonical_aggregate_count(client) -> int:
    return _aggregate_count(client, "context-layer:evidence:canonical:*")


def inspect(client) -> dict:
    return {
        "current_version": client.get(MIGRATION_KEY),
        "legacy_raw_aggregates": _aggregate_count(client, "context-layer:evidence:aggregate:*") ,
        "canonical_aggregates": _canonical_aggregate_count(client),
        "observation_events": sum(1 for _ in client.scan_iter(match="context-layer:evidence:observation:*")),
    }


def apply(client) -> dict:
    previous = client.get(MIGRATION_KEY)
    client.set(
        MIGRATION_KEY,
        json.dumps({
            "version": TARGET_VERSION,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "strategy": "non_destructive_canonical_cutover",
        }, sort_keys=True),
    )
    result = inspect(client)
    result["previous_version"] = previous
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Record the idempotent v2 cutover marker.")
    args = parser.parse_args()
    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    result = apply(client) if args.apply else inspect(client)
    result["applied"] = bool(args.apply)
    result["target_version"] = TARGET_VERSION
    result["note"] = (
        "Legacy raw aggregates are retained until TTL expiry and are not used "
        "for canonical PromotionPolicy evaluation."
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
