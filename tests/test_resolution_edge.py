from polymarketpulse.prediction.resolution_edge import compute_resolution_edge


def test_clear_resolution_with_deadline_and_authority_scores_low_risk() -> None:
    question = "Will the ceasefire agreement be officially signed by March 1, 2026?"
    resolution_text = 'This market resolves to "Yes" if the ceasefire agreement is officially signed and confirmed by March 1, 2026.'
    result = compute_resolution_edge(question, resolution_text, authority_source="state_department")
    assert result.has_explicit_deadline is True
    assert result.risk_level in ("niedrig", "mittel")
    assert result.resolution_edge_score > 40


def test_vague_resolution_without_deadline_or_authority_scores_high_risk() -> None:
    question = "Will the situation significantly improve?"
    result = compute_resolution_edge(question, None, authority_source=None)
    assert result.has_explicit_deadline is False
    assert result.risk_level == "hoch"
    assert "Keine explizite Frist" in " ".join(result.pitfalls)


def test_ambiguous_terms_increase_ambiguity_score() -> None:
    clear = compute_resolution_edge("Will X win?", "Resolves Yes if X wins the election.", authority_source="ec")
    vague = compute_resolution_edge(
        "Will X win?", "Resolves Yes if X is widely regarded as having significant support.", authority_source="ec"
    )
    assert vague.ambiguity_score > clear.ambiguity_score
