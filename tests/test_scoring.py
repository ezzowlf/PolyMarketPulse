from datetime import UTC, datetime, timedelta

from polymarketpulse.models import Market
from polymarketpulse.scoring import opportunity_score


def _market(**overrides) -> Market:
    now = datetime.now(UTC)
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "0xabc",
        "question": "Test market",
        "slug": "test-market",
        "end_at": now + timedelta(days=30),
        "start_at": now - timedelta(days=30),
        "updated_at": now,
        "yes_price": 0.5,
        "no_price": 0.5,
        "yes_token_id": "111",
        "no_token_id": "222",
        "best_bid": 0.49,
        "best_ask": 0.51,
        "liquidity": 100000,
        "volume_24h": 50000,
        "volume_total": 500000,
        "spread": 0.01,
        "one_day_change": 0.08,
        "category": None,
        "url": "https://polymarket.com/event/test-market",
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_liquid_active_market_scores_higher() -> None:
    strong = _market()
    weak = _market(
        provider_market_id="2",
        question="Weak",
        slug="weak",
        yes_price=0.99,
        no_price=0.01,
        liquidity=100,
        volume_24h=10,
        volume_total=10,
        spread=0.20,
        one_day_change=0.0,
    )
    assert opportunity_score(strong).score > opportunity_score(weak).score


def test_missing_fields_penalize_score() -> None:
    complete = _market()
    incomplete = _market(provider_market_id="3", missing_fields=("spread", "bestBid", "bestAsk"))
    assert opportunity_score(incomplete).score < opportunity_score(complete).score


def test_score_stays_within_bounds() -> None:
    market = _market(liquidity=10_000_000, volume_24h=5_000_000, spread=0.001, one_day_change=0.5)
    result = opportunity_score(market)
    assert 0.0 <= result.score <= 100.0


def test_subfactors_present_for_triggered_rules() -> None:
    market = _market()
    result = opportunity_score(market)
    assert "liquidity" in result.subfactors
    assert "volume_24h" in result.subfactors
