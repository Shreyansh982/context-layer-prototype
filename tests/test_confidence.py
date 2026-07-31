from app.services import confidence


def test_source_certainty_detects_hedging():
    assert confidence.source_certainty("I might prefer remote work") < confidence.source_certainty("I prefer remote work")


def test_similarity_corroboration_uses_distance_boundaries():
    assert confidence.similarity_corroboration(0.0) == 1.0
    assert confidence.similarity_corroboration(1.0) == 0.0


def test_structural_completeness_requires_known_identifier(monkeypatch):
    monkeypatch.setattr(confidence, "known_identifiers", lambda: {"E0001", "D10"})
    assert confidence.structural_completeness("E0001 is assigned to D10") == 1.0
    assert confidence.structural_completeness("E9999 is assigned") == 0.0


def test_combined_score_clamps_at_zero_and_one(monkeypatch):
    monkeypatch.setattr(confidence, "source_certainty", lambda _content: 10.0)
    monkeypatch.setattr(confidence, "similarity_corroboration", lambda _distance: 10.0)
    monkeypatch.setattr(confidence, "structural_completeness", lambda _content: 10.0)
    assert confidence.score("fact", "E0001", 100, 0.0) == 1.0
    monkeypatch.setattr(confidence, "source_certainty", lambda _content: -10.0)
    monkeypatch.setattr(confidence, "similarity_corroboration", lambda _distance: -10.0)
    monkeypatch.setattr(confidence, "structural_completeness", lambda _content: -10.0)
    assert confidence.score("fact", "E0001", -100, 1.0) == 0.0
