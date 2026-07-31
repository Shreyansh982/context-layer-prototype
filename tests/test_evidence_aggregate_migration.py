"""Regression coverage for the non-destructive evidence aggregate cutover."""

import json

from scripts.migrate_evidence_aggregates import MIGRATION_KEY, TARGET_VERSION, apply, inspect


class _Redis:
    def __init__(self):
        self.values = {
            "context-layer:evidence:aggregate:legacy": "legacy",
            "context-layer:evidence:canonical:fact-1": "canonical",
            "context-layer:evidence:observation:obs-1": "event",
        }

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def scan_iter(self, match):
        prefix = match.rstrip("*")
        return (key for key in self.values if key.startswith(prefix))


def test_v2_cutover_is_non_destructive_and_idempotent():
    client = _Redis()
    before = inspect(client)
    first = apply(client)
    second = apply(client)

    assert before["legacy_raw_aggregates"] == 1
    assert first["legacy_raw_aggregates"] == 1
    assert second["canonical_aggregates"] == 1
    marker = json.loads(client.get(MIGRATION_KEY))
    assert marker["version"] == TARGET_VERSION
    assert marker["strategy"] == "non_destructive_canonical_cutover"
