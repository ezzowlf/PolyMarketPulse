from datetime import UTC, datetime, timedelta

from polymarketpulse.models import Market
from polymarketpulse.signals import (
    DATA_QUALITY_WARNING,
    LIQUIDITY_SURGE,
    NEW_MARKET,
    RESOLUTION_APPROACHING,
    SPREAD_COMPRESSION,
    PreviousSnapshot,
    generate_signals,
)


def _market(**overrides) -> Market:
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "0xabc",
        "question": "Test market",
        "slug": "test-market",
        "liquidity": 50000,
        "volume_24h": 20000,
        "volume_total": 200000,
        "spread": 0.03,
        "yes_price": 0.5,
        "one_day_change": 0.01,
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_data_quality_warning_emitted_for_missing_fields() -> None:
    market = _market(missing_fields=("spread",))
    signals = generate_signals(market)
    assert any(s.signal_type == DATA_QUALITY_WARNING for s in signals)


def test_new_market_signal_for_recent_start() -> None:
    now = datetime.now(UTC)
    market = _market(start_at=now - timedelta(hours=5))
    signals = generate_signals(market, now=now)
    assert any(s.signal_type == NEW_MARKET for s in signals)


def test_resolution_approaching_signal() -> None:
    now = datetime.now(UTC)
    market = _market(end_at=now + timedelta(days=1))
    signals = generate_signals(market, now=now)
    assert any(s.signal_type == RESOLUTION_APPROACHING for s in signals)


def test_liquidity_surge_requires_previous_snapshot() -> None:
    market = _market(liquidity=100000)
    previous = PreviousSnapshot(liquidity=50000, volume_24h=None, spread=None, yes_price=None, one_day_change=None)
    signals = generate_signals(market, previous=previous)
    assert any(s.signal_type == LIQUIDITY_SURGE for s in signals)

    signals_no_history = generate_signals(market, previous=None)
    assert not any(s.signal_type == LIQUIDITY_SURGE for s in signals_no_history)


def test_spread_compression_signal() -> None:
    market = _market(spread=0.01)
    previous = PreviousSnapshot(liquidity=None, volume_24h=None, spread=0.05, yes_price=None, one_day_change=None)
    signals = generate_signals(market, previous=previous)
    assert any(s.signal_type == SPREAD_COMPRESSION for s in signals)


def test_no_signal_language_implies_guaranteed_outcome() -> None:
    market = _market(missing_fields=("spread",))
    signals = generate_signals(market)
    forbidden = ("sicherer gewinn", "garantiert", "jetzt kaufen", "jetzt setzen")
    for signal in signals:
        text = " ".join(signal.reasons).lower()
        for phrase in forbidden:
            assert phrase not in text
