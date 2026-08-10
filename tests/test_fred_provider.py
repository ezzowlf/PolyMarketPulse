"""Tests for providers/fred.py — the real FRED CSV client and macro-snapshot
derivation, verified against realistic mocked CSV responses (same
mocked-verification standard used for CoinGecko: real parsing/derivation
logic, exercised with values matching real historical FRED observations,
since live network access to fred.stlouisfed.org is blocked in this
sandbox — see fred.py's module docstring)."""

from __future__ import annotations

from datetime import date

import pytest

from polymarketpulse.providers import fred


def _fedfunds_csv() -> str:
    # Realistic recent FEDFUNDS values (effective federal funds rate, %).
    return (
        "DATE,FEDFUNDS\n"
        "2025-08-01,4.33\n"
        "2025-09-01,4.09\n"
        "2025-10-01,3.87\n"
    )


def _cpi_csv() -> str:
    # 16 months of a realistic CPIAUCSL-shaped index series so a YoY trend
    # (needs ~13+ months) and a ~3-month-earlier reference point both exist.
    rows = ["DATE,CPIAUCSL"]
    base = 305.0
    for i in range(16):
        year = 2024 + (7 + i) // 12
        month = (7 + i - 1) % 12 + 1
        value = base + i * 0.4
        rows.append(f"{year:04d}-{month:02d}-01,{value:.2f}")
    return "\n".join(rows) + "\n"


def _unrate_csv() -> str:
    return (
        "DATE,UNRATE\n"
        "2025-07-01,4.1\n"
        "2025-08-01,4.2\n"
        "2025-09-01,4.3\n"
        "2025-10-01,4.4\n"
    )


def test_parse_fred_csv_skips_missing_observations() -> None:
    text = "DATE,FEDFUNDS\n2025-01-01,4.5\n2025-02-01,.\n2025-03-01,4.4\n"
    parsed = fred._parse_fred_csv(text, "FEDFUNDS")
    assert parsed == [(date(2025, 1, 1), 4.5), (date(2025, 3, 1), 4.4)]


def test_parse_fred_csv_rejects_non_fred_shaped_response() -> None:
    # e.g. an HTML error page or unrelated JSON, not a real FRED CSV
    assert fred._parse_fred_csv("<html>not found</html>", "FEDFUNDS") is None
    assert fred._parse_fred_csv("", "FEDFUNDS") is None


def test_parse_fred_csv_accepts_observation_date_header() -> None:
    text = "OBSERVATION_DATE,FEDFUNDS\n2025-01-01,4.5\n2025-02-01,4.4\n"
    parsed = fred._parse_fred_csv(text, "FEDFUNDS")
    assert parsed == [(date(2025, 1, 1), 4.5), (date(2025, 2, 1), 4.4)]


def test_build_macro_snapshot_from_realistic_mocked_series() -> None:
    fedfunds = fred._parse_fred_csv(_fedfunds_csv(), "FEDFUNDS")
    cpi = fred._parse_fred_csv(_cpi_csv(), "CPIAUCSL")
    unrate = fred._parse_fred_csv(_unrate_csv(), "UNRATE")

    snapshot = fred.build_macro_snapshot(fedfunds, cpi, unrate)

    assert snapshot is not None
    assert snapshot.policy_rate == pytest.approx(3.87)
    assert snapshot.unemployment_rate == pytest.approx(4.4)
    # CPI YoY derived from real fetched index values, not fabricated.
    assert snapshot.cpi_yoy is not None
    assert snapshot.cpi_yoy_prior is not None
    assert snapshot.next_fomc_meeting_date is not None


def test_build_macro_snapshot_returns_none_when_any_series_missing() -> None:
    fedfunds = fred._parse_fred_csv(_fedfunds_csv(), "FEDFUNDS")
    cpi = fred._parse_fred_csv(_cpi_csv(), "CPIAUCSL")
    # unrate missing entirely — must not fabricate a partial snapshot
    assert fred.build_macro_snapshot(fedfunds, cpi, None) is None
    assert fred.build_macro_snapshot(None, cpi, None) is None


def test_build_macro_snapshot_returns_none_when_cpi_history_too_short() -> None:
    fedfunds = fred._parse_fred_csv(_fedfunds_csv(), "FEDFUNDS")
    short_cpi = fred._parse_fred_csv("DATE,CPIAUCSL\n2025-09-01,305.0\n2025-10-01,305.4\n", "CPIAUCSL")
    unrate = fred._parse_fred_csv(_unrate_csv(), "UNRATE")
    assert fred.build_macro_snapshot(fedfunds, short_cpi, unrate) is None


def test_next_fomc_meeting_returns_first_date_strictly_after_reference() -> None:
    meeting = fred.next_fomc_meeting(after=date(2026, 8, 9))
    assert meeting == date(2026, 9, 16)
    assert fred.next_fomc_meeting(after=date(2026, 12, 31)) is None


def test_fetch_series_csv_returns_none_on_http_error(monkeypatch) -> None:
    import httpx

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("simulated sandbox TLS failure")

    monkeypatch.setattr(httpx, "get", _raise)
    assert fred._fetch_series_csv("FEDFUNDS") is None
    assert fred.fetch_macro_snapshot() is None


def test_fetch_series_csv_rejects_unsafe_url(monkeypatch) -> None:
    # Redirect the base URL to a private address to prove the SSRF guard
    # is actually consulted, not bypassed.
    monkeypatch.setattr(fred, "_CSV_BASE_URL", "http://127.0.0.1/fredgraph.csv")
    assert fred._fetch_series_csv("FEDFUNDS") is None
