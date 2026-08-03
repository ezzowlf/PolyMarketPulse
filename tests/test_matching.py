from polymarketpulse.matching import (
    MarketMatchCandidate,
    compute_divergence,
    find_candidate_matches,
    text_similarity,
)
from polymarketpulse.models import Market


def _market(**overrides) -> Market:
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "",
        "question": "Will the Fed cut rates in September?",
        "slug": "fed-cut",
        "yes_price": 0.61,
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_text_similarity_identical_is_one() -> None:
    assert text_similarity("Will the Fed cut rates?", "Will the Fed cut rates?") == 1.0


def test_text_similarity_unrelated_is_low() -> None:
    assert text_similarity("Will the Fed cut rates?", "Will it rain in Paris?") < 0.3


def test_find_candidate_matches_only_returns_candidate_status() -> None:
    a = _market(provider="polymarket", question="Will the Fed cut interest rates in September 2026?")
    b = _market(provider="kalshi", provider_market_id="2", question="Will the Fed cut interest rates in September?")
    candidates = find_candidate_matches([a], [b], min_text_similarity=0.3)
    assert len(candidates) == 1
    assert candidates[0].status == "candidate"


def test_find_candidate_matches_excludes_dissimilar() -> None:
    a = _market(question="Will the Fed cut rates?")
    b = _market(provider="kalshi", provider_market_id="2", question="Will it snow in December?")
    candidates = find_candidate_matches([a], [b])
    assert candidates == []


def test_compute_divergence_reports_price_gap() -> None:
    a = _market(yes_price=0.61)
    b = _market(provider="kalshi", provider_market_id="2", yes_price=0.57)
    candidate = MarketMatchCandidate(
        market_a=a, market_b=b, text_similarity=0.9, date_similarity=None,
        outcome_structure_match=None, category_match=None,
    )
    divergence = compute_divergence(candidate)
    assert round(divergence.divergence, 2) == 0.04
    assert divergence.status == "candidate"


def test_compute_divergence_handles_missing_prices() -> None:
    a = _market(yes_price=None)
    b = _market(provider="kalshi", provider_market_id="2", yes_price=0.5)
    candidate = MarketMatchCandidate(
        market_a=a, market_b=b, text_similarity=0.9, date_similarity=None,
        outcome_structure_match=None, category_match=None,
    )
    divergence = compute_divergence(candidate)
    assert divergence.divergence is None
