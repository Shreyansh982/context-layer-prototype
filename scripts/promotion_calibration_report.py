"""Read passive promotion-comparison JSONL files; never change application state."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


CANDIDATES = {
    "parity_control": {"observations": 1, "sessions": 0, "threshold": 0.60},
    "intermediate": {"observations": 2, "sessions": 2, "threshold": 0.65},
    "conservative": {"observations": 3, "sessions": 3, "threshold": 0.70},
}


def _clamp(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _bucket_confidence(value) -> str:
    if value is None:
        return "missing"
    value = float(value)
    if value < 0.5:
        return "0.0-0.49"
    if value < 0.8:
        return "0.5-0.79"
    return "0.8-1.0"


def _candidate_promotes(record: dict, config: dict) -> bool:
    if record.get("authoritative_enterprise"):
        return True
    observation_strength = _clamp((record.get("observation_count") or 0) / config["observations"])
    session_strength = 1.0 if config["sessions"] == 0 else _clamp((record.get("distinct_session_count") or 0) / config["sessions"])
    corroboration = _clamp(max((record.get("source_count") or 0) - 1, 0) / 2)
    score = (
        0.25 * float(record.get("effective_confidence") or 0.0)
        + 0.25 * observation_strength
        + 0.20 * session_strength
        + 0.10 * corroboration
        + 0.10 * float(record.get("source_quality") or 0.40)
        + 0.05 * (1.0 if record.get("provenance_present") else 0.5)
        + 0.05 * float(record.get("recency_strength") or 0.0)
    )
    return (
        (record.get("observation_count") or 0) >= config["observations"]
        and (record.get("distinct_session_count") or 0) >= config["sessions"]
        and score >= config["threshold"]
    )


def _comparison_metrics(records: list[dict], promotes) -> dict:
    eligible = [record for record in records if record.get("writer_outcome") in {"PROMOTED", "NOT_PROMOTED"}]
    outcomes = [(promotes(record), record["writer_outcome"] == "PROMOTED") for record in eligible]
    total = len(outcomes)
    agreement = sum(policy == writer for policy, writer in outcomes)
    return {
        "evaluated": total,
        "promote_recommendations": sum(policy for policy, _ in outcomes),
        "collect_more_evidence_recommendations": sum(not policy for policy, _ in outcomes),
        "agreement_rate": round(agreement / total, 6) if total else None,
        "disagreement_rate": round((total - agreement) / total, 6) if total else None,
        "false_promotions": sum(policy and not writer for policy, writer in outcomes),
        "missed_promotions": sum(not policy and writer for policy, writer in outcomes),
    }


def load_records(directory: Path, cohort: str | None = "live") -> list[dict]:
    latest: dict[str, dict] = {}
    for path in directory.glob("*-promotion-comparison-*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if cohort is not None and record.get("cohort") != cohort:
                continue
            key = record["comparison_key"]
            if key not in latest or record.get("captured_at", "") >= latest[key].get("captured_at", ""):
                latest[key] = record
    return list(latest.values())


def report(records: list[dict]) -> dict:
    latest: dict[str, dict] = {}
    for record in records:
        key = record["comparison_key"]
        if key not in latest or record.get("captured_at", "") >= latest[key].get("captured_at", ""):
            latest[key] = record
    records = list(latest.values())
    decisions = [record for record in records if record.get("decision_available")]
    current = _comparison_metrics(decisions, lambda record: record.get("recommended_action") == "PROMOTE")
    return {
        "total_logical_observations": len(records),
        "decision_available": len(decisions),
        "writer_failed": sum(record.get("writer_outcome") == "WRITER_FAILED" for record in records),
        "current_shadow_policy": current,
        "source_distribution": dict(Counter(record.get("observation_source", "missing") for record in records)),
        "confidence_distribution": dict(Counter(_bucket_confidence(record.get("effective_confidence")) for record in records)),
        "session_count_distribution": dict(Counter(record.get("distinct_session_count") or 0 for record in records)),
        "candidate_profiles": {
            name: _comparison_metrics(decisions, lambda record, config=config: _candidate_promotes(record, config))
            for name, config in CANDIDATES.items()
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="logs", help="Directory containing promotion-comparison JSONL files")
    parser.add_argument("--cohort", default="live", help="Capture cohort to report; use 'all' to include every cohort")
    args = parser.parse_args()
    cohort = None if args.cohort == "all" else args.cohort
    print(json.dumps(report(load_records(Path(args.directory), cohort)), indent=2, sort_keys=True))
