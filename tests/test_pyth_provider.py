"""Pyth Hermes WTI provider (Block A-D of the WTI $85 price-target-engine
work): real active-month roll-rule math + a mocked (no live network in the
test suite) current-price fetch. Live verification against the real Pyth
production API happened during development (see
analysis/reports/wti_price_target_baseline.md) -- these tests only assert
the pure, reproducible logic."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from polymarketpulse.providers.pyth import (
    active_month_contract,
    active_month_symbol,
    fetch_latest_price,
    last_trading_day,
)

# ---------------------------------------------------------------------
# Active-month roll rule -- the exact worked example from the real
# resolution text: "if the 25th of the month is a Saturday, the last
# trading session for the nearest listed contract is the session for
# Tuesday the 21st, and the next listed contract becomes the active month
# at the start of the trading session for Friday the 17th."
# ---------------------------------------------------------------------


def test_resolution_text_worked_example_25th_on_saturday() -> None:
    # August 2026: the 25th is a Tuesday, not a Saturday -- pick a real
    # month where the 25th genuinely falls on Saturday to reproduce the
    # exact worked example. October 2026: the 25th is a Sunday... use a
    # month with Saturday the 25th: January 2027's 25th is a Monday. Find
    # one directly: July 2026's 25th is a Saturday.
    assert date(2026, 7, 25).weekday() == 5  # Saturday, confirms the fixture
    ltd = last_trading_day(2026, 8)  # delivery month August -> preceding month July
    assert ltd == date(2026, 7, 21)  # "Tuesday the 21st"
    assert ltd.weekday() == 1  # Tuesday


def test_wti_september_2026_last_trading_day_matches_live_pyth_feed_metadata() -> None:
    """Cross-checked live during development: Pyth's own feed description
    for the WTIU6 (September 2026 delivery) feed id literally reads "PYTH
    WTI 20 AUGUST 2026" -- this must match our own computed last trading
    day exactly, an independent real-world validation of the roll math."""
    assert last_trading_day(2026, 9) == date(2026, 8, 20)


def test_active_month_before_and_after_the_september_contract_roll() -> None:
    """August 17, 2026 (one day before the computed roll date) must still
    resolve to the September contract (WTIU6); August 18 (the roll date
    itself) must already resolve to October (WTIV6)."""
    assert active_month_contract(date(2026, 8, 17)) == (2026, 9)
    assert active_month_symbol(date(2026, 8, 17)) == "WTIU6"
    assert active_month_contract(date(2026, 8, 18)) == (2026, 10)
    assert active_month_symbol(date(2026, 8, 18)) == "WTIV6"


def test_active_month_mid_contract_life_is_stable() -> None:
    """A date comfortably inside a contract's active window (well before
    its own roll date) must resolve to that same contract, not drift."""
    assert active_month_symbol(date(2026, 8, 1)) == "WTIU6"
    assert active_month_symbol(date(2026, 9, 1)) == "WTIV6"


def test_expired_nearest_contract_is_skipped_not_selected() -> None:
    """A contract whose own last trading day has already passed must never
    be returned as the active month, even if it's nominally "closest" by
    delivery month -- the July 2026 WTI contract (delivery month 7) has
    already expired by August 17, 2026, so it must be skipped entirely."""
    year, month = active_month_contract(date(2026, 8, 17))
    ltd_of_result = last_trading_day(year, month)
    assert ltd_of_result >= date(2026, 8, 17)


# ---------------------------------------------------------------------
# fetch_latest_price -- mocked HTTP, no live network in the test suite.
# ---------------------------------------------------------------------


def _mock_response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.content = b"x" * 100
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_latest_price_parses_real_hermes_response_shape() -> None:
    # Real shape observed live against https://hermes.pyth.network during
    # development (WTIU6 feed, ~$84.38).
    payload = {
        "parsed": [{
            "id": "17d0b3b03f9ccb6bb6721960f034b8601b3d89ef70743b33f86304a1565cebda",
            "price": {"price": "8438159", "conf": "542", "expo": -5, "publish_time": 1786994441},
        }]
    }
    with patch("polymarketpulse.providers.pyth.httpx.get", return_value=_mock_response(payload)):
        result = fetch_latest_price("17d0b3b03f9ccb6bb6721960f034b8601b3d89ef70743b33f86304a1565cebda")
    assert result is not None
    assert abs(result.price - 84.38159) < 1e-6
    assert abs(result.confidence - 0.00542) < 1e-6
    assert result.publish_time.year == 2026


def test_fetch_latest_price_returns_none_on_network_error() -> None:
    import httpx

    with patch("polymarketpulse.providers.pyth.httpx.get", side_effect=httpx.ConnectError("boom")):
        assert fetch_latest_price("deadbeef") is None


def test_fetch_latest_price_returns_none_on_empty_parsed() -> None:
    with patch("polymarketpulse.providers.pyth.httpx.get", return_value=_mock_response({"parsed": []})):
        assert fetch_latest_price("deadbeef") is None


def test_fetch_latest_price_returns_none_on_malformed_payload() -> None:
    with patch("polymarketpulse.providers.pyth.httpx.get", return_value=_mock_response({"unexpected": "shape"})):
        assert fetch_latest_price("deadbeef") is None


def test_fetch_latest_price_never_fabricates_on_zero_or_negative_price() -> None:
    payload = {"parsed": [{"id": "x", "price": {"price": "-100", "conf": "1", "expo": -2, "publish_time": 1786994441}}]}
    with patch("polymarketpulse.providers.pyth.httpx.get", return_value=_mock_response(payload)):
        assert fetch_latest_price("x") is None
