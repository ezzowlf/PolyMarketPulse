from polymarketpulse.models import Market
from polymarketpulse.shadow import MIN_CONFIRMING_FACTORS, evaluate_shadow_setup
from polymarketpulse.signals import PreviousSnapshot


def _market(**overrides) -> Market:
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "",
        "question": "Test market",
        "slug": "test",
        "liquidity": 50000,
        "volume_24h": 20000,
        "yes_price": 0.5,
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_quiet_market_does_not_qualify() -> None:
    setup = evaluate_shadow_setup(_market(liquidity=1000, volume_24h=100))
    assert setup.qualifies is False


def test_multiple_confirming_factors_qualify() -> None:
    market = _market(liquidity=200000, yes_price=0.6)
    previous = PreviousSnapshot(liquidity=200000, volume_24h=10000, spread=0.02, yes_price=0.45, one_day_change=0.1)
    setup = evaluate_shadow_setup(
        market, previous=previous, data_quality_score=95, news_count=2, news_max_confidence=0.8
    )
    assert setup.confirming_factor_count >= MIN_CONFIRMING_FACTORS
    assert setup.qualifies is True


def test_reasons_are_german_and_human_readable() -> None:
    market = _market(liquidity=200000, yes_price=0.6)
    previous = PreviousSnapshot(liquidity=200000, volume_24h=10000, spread=0.02, yes_price=0.45, one_day_change=0.1)
    setup = evaluate_shadow_setup(market, previous=previous, data_quality_score=95)
    assert setup.warum_interessant
    for reason in setup.warum_interessant:
        assert "Signal" not in reason  # never a raw technical signal name
        assert not reason.replace(".", "").replace("%", "").replace("$", "").replace(",", "").strip().isdigit()


def test_missing_data_is_reported_in_was_fehlt() -> None:
    setup = evaluate_shadow_setup(_market(liquidity=1000))
    assert setup.was_fehlt


def test_breakdown_components_sum_to_score() -> None:
    market = _market(liquidity=200000, yes_price=0.6)
    previous = PreviousSnapshot(liquidity=200000, volume_24h=10000, spread=0.02, yes_price=0.45, one_day_change=0.1)
    setup = evaluate_shadow_setup(market, previous=previous, data_quality_score=95)
    assert setup.score == setup.breakdown.total


def test_score_never_exceeds_100() -> None:
    market = _market(liquidity=5_000_000, yes_price=0.9)
    previous = PreviousSnapshot(liquidity=100000, volume_24h=1000, spread=0.02, yes_price=0.1, one_day_change=0.5)
    setup = evaluate_shadow_setup(
        market,
        previous=previous,
        data_quality_score=100,
        news_count=10,
        news_max_confidence=0.9,
        comparable_market_count=5,
        cross_provider_divergence=0.2,
    )
    assert 0 <= setup.score <= 100 + 1e-9  # breakdown caps are generous but bounded per-component


def test_no_forbidden_language_in_reasons() -> None:
    market = _market(liquidity=200000, yes_price=0.6)
    previous = PreviousSnapshot(liquidity=200000, volume_24h=10000, spread=0.02, yes_price=0.45, one_day_change=0.1)
    setup = evaluate_shadow_setup(market, previous=previous, data_quality_score=95)
    forbidden = ("sicherer gewinn", "garantiert", "jetzt kaufen", "jetzt wetten")
    all_text = " ".join(setup.warum_interessant + setup.warum_nicht + setup.was_fehlt).lower()
    for phrase in forbidden:
        assert phrase not in all_text
