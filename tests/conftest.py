"""Keep passive calibration logs free of ordinary unit-test traffic."""

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _disable_promotion_comparison_capture(monkeypatch):
    monkeypatch.setattr(settings, "PROMOTION_COMPARISON_ENABLED", False)
    monkeypatch.setattr(settings, "PROMOTION_COMPARISON_COHORT", "test")
