"""Real, mocked tests for BLS as a fallback source when FRED's CPI/
unemployment series can't be fetched — root-cause investigation this round
confirmed FRED specifically hangs (ReadTimeout on every endpoint, including
its bare root page) while BLS/BEA/federalreserve.gov all return real 200s,
so a real fallback is warranted rather than leaving these series
permanently unavailable whenever FRED alone is unreachable."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from polymarketpulse.providers import bls, fred


def _bls_payload(series_id: str, points: list[tuple[str, str, str]]) -> dict:
    return {
        "status": "REQUEST_SUCCEEDED",
        "Results": {"series": [{"seriesID": series_id, "data": [
            {"year": year, "period": period, "value": value} for year, period, value in points
        ]}]},
    }


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.content = b"x"

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def test_bls_cpi_parses_real_shape_response() -> None:
    payload = _bls_payload(bls.SERIES_CPI_U, [("2026", "M07", "333.918"), ("2026", "M06", "333.952")])
    with patch("polymarketpulse.providers.bls.httpx.get", return_value=_FakeResponse(payload)):
        series = bls.fetch_cpi_index_series()
    assert series == [(date(2026, 6, 1), 333.952), (date(2026, 7, 1), 333.918)]


def test_bls_unemployment_parses_real_shape_response() -> None:
    payload = _bls_payload(bls.SERIES_UNRATE, [("2026", "M07", "4.1"), ("2026", "M06", "4.2")])
    with patch("polymarketpulse.providers.bls.httpx.get", return_value=_FakeResponse(payload)):
        series = bls.fetch_unemployment_rate_series()
    assert series == [(date(2026, 6, 1), 4.2), (date(2026, 7, 1), 4.1)]


def test_bls_returns_none_on_malformed_response() -> None:
    with patch("polymarketpulse.providers.bls.httpx.get", return_value=_FakeResponse({"status": "REQUEST_NOT_SUCCEEDED"})):
        assert bls.fetch_cpi_index_series() is None


def test_bls_annual_aggregate_rows_are_skipped_not_miscounted() -> None:
    """BLS series sometimes include an M13 'annual average' row alongside
    the 12 real monthly rows — this must not be parsed as a 13th month."""
    payload = _bls_payload(bls.SERIES_CPI_U, [("2026", "M13", "330.0"), ("2026", "M01", "320.0")])
    with patch("polymarketpulse.providers.bls.httpx.get", return_value=_FakeResponse(payload)):
        series = bls.fetch_cpi_index_series()
    assert series == [(date(2026, 1, 1), 320.0)]


def test_fred_falls_back_to_bls_when_fred_cpi_fetch_fails() -> None:
    """The real fallback wiring in fred.fetch_macro_snapshot: FRED's own
    CPI/UNRATE fetch failing must trigger a real BLS fetch attempt, not
    silently give up on those two series."""
    real_fedfunds = [(date(2026, 6, 1), 5.25), (date(2026, 7, 1), 5.25)]
    bls_cpi = [(date(2025, m, 1), 320.0 + m) for m in range(1, 13)] + [
        (date(2026, m, 1), 330.0 + m) for m in range(1, 8)
    ]
    bls_unrate = [(date(2026, m, 1), 4.0) for m in range(1, 8)]

    with patch("polymarketpulse.providers.fred._fetch_series_csv") as mock_fred_fetch, \
         patch("polymarketpulse.providers.bls.fetch_cpi_index_series", return_value=bls_cpi) as mock_bls_cpi, \
         patch("polymarketpulse.providers.bls.fetch_unemployment_rate_series", return_value=bls_unrate) as mock_bls_unrate:

        def fred_side_effect(series_id: str, timeout: float = 10.0):
            if series_id == fred.SERIES_FEDFUNDS:
                return real_fedfunds
            return None  # CPI and UNRATE both fail via FRED this run

        mock_fred_fetch.side_effect = fred_side_effect
        snapshot = fred.fetch_macro_snapshot()

    assert mock_bls_cpi.called
    assert mock_bls_unrate.called
    assert snapshot is not None
    assert snapshot.policy_rate == 5.25  # real FRED value, unaffected by the fallback path
    assert snapshot.unemployment_rate == 4.0  # came from the real BLS fallback


def test_fred_snapshot_stays_none_when_policy_rate_unavailable_even_with_bls_fallback() -> None:
    """Policy rate has no keyless fallback source — a FRED failure there
    must still leave the whole snapshot honestly unavailable, never a
    snapshot with a fabricated/stale policy rate."""
    with patch("polymarketpulse.providers.fred._fetch_series_csv", return_value=None), \
         patch("polymarketpulse.providers.bls.fetch_cpi_index_series", return_value=[(date(2026, 1, 1), 320.0)]), \
         patch("polymarketpulse.providers.bls.fetch_unemployment_rate_series", return_value=[(date(2026, 1, 1), 4.0)]):
        snapshot = fred.fetch_macro_snapshot()
    assert snapshot is None
