"""Deterministic confidence scoring for durable memories."""

import re

from app.services.knowledge import knowledge_service

HEDGE_PATTERN = re.compile(r"\b(?:maybe|might|perhaps|possibly|probably|guess|unsure)\b", re.I)
IDENTIFIER_PATTERN = re.compile(r"\b(?:E\d{4}|[A-Z]+\d+)\b", re.I)


def source_certainty(content: str) -> float:
    """Reduce certainty when a fact is explicitly hedged."""
    return 0.25 if HEDGE_PATTERN.search(content) else 1.0


def similarity_corroboration(nearest_distance: float | None) -> float:
    """Map normalized Chroma distance to corroboration strength."""
    if nearest_distance is None:
        return 0.0
    return clamp(1.0 - nearest_distance, 0.0, 1.0)


def known_identifiers() -> set[str]:
    """Read authoritative employee and department IDs from enterprise knowledge."""
    identifiers = set()
    for dataframe in knowledge_service.loader.get_all_sheets().values():
        for column in ("EMP_ID", "DEPT_ID"):
            if column in dataframe.columns:
                identifiers.update(dataframe[column].dropna().astype(str).str.upper())
    return identifiers


def structural_completeness(content: str) -> float:
    """Reward facts that reference a real employee or department identifier."""
    referenced = {match.upper() for match in IDENTIFIER_PATTERN.findall(content)}
    return 1.0 if referenced & known_identifiers() else 0.0


def clamp(value: float, lower: float, upper: float) -> float:
    """Constrain a score to an inclusive range."""
    return max(lower, min(value, upper))


def score(content: str, owner_id: str, times_observed: int, nearest_distance: float | None) -> float:
    """Compute confidence from deterministic evidence only."""
    del owner_id  # Reserved for future owner-specific evidence without altering the API.
    confidence = (
        0.4 * source_certainty(content)
        + 0.3 * similarity_corroboration(nearest_distance)
        + 0.2 * min(max(times_observed, 0) / 3, 1.0)
        + 0.1 * structural_completeness(content)
    )
    return clamp(confidence, 0.0, 1.0)
