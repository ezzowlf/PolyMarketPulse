from datetime import UTC, datetime

from polymarketpulse.models import Market, ResolutionStatus, Signal
from polymarketpulse.telegram import (
    format_daily_stats,
    format_provider_outage,
    format_resolution,
    format_signal,
)


def _market(**overrides) -> Market:
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "",
        "question": "Will X happen?",
        "slug": "will-x-happen",
        "liquidity": 50000,
        "volume_24h": 20000,
        "yes_price": 0.65,
        "spread": 0.02,
        "url": "https://polymarket.com/event/will-x-happen",
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_format_signal_contains_disclaimer_and_no_forbidden_language() -> None:
    market = _market()
    signal = Signal(market=market, signal_type="PRICE_MOMENTUM", score=80.0, reasons=("starke Bewegung",))
    text = format_signal(signal)
    assert "keine Wettaufforderung" in text
    # The disclaimer explicitly negates "sicherer Gewinn" ("kein sicherer
    # Gewinn"); what must never appear is an *unqualified* claim of it.
    assert "kein sicherer gewinn" in text.lower()
    for phrase in ("garantiert", "jetzt kaufen", "jetzt setzen"):
        assert phrase not in text.lower()


def test_format_resolution_includes_outcome() -> None:
    market = _market(
        resolution_status=ResolutionStatus.RESOLVED,
        winning_outcome="Yes",
        resolved_at=datetime.now(UTC),
    )
    text = format_resolution(market)
    assert "Yes" in text
    assert "AUFGELÖST" in text


def test_format_daily_stats_renders_all_keys() -> None:
    text = format_daily_stats({"Märkte": 10, "Signale": 3})
    assert "Märkte: 10" in text
    assert "Signale: 3" in text


def test_format_provider_outage_names_provider_and_error() -> None:
    text = format_provider_outage("polymarket", "timeout")
    assert "polymarket" in text
    assert "timeout" in text
    assert "keine Handlungsaufforderung" in text
