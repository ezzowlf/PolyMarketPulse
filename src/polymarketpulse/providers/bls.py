"""BLS (Bureau of Labor Statistics) provider — free, keyless public API,
used as a real fallback source when FRED itself is unreachable.

Root-cause investigation (this round): `fred.stlouisfed.org` completes a
real TCP+TLS handshake instantly but then never returns a response body
(httpx.ReadTimeout on every endpoint tried, including the bare root page)
— consistent with a local network intermediary intercepting the TLS
session and then failing to forward it, not a certificate issue, not a
FRED-side rate limit (which would return a fast 429), not a parsing bug
(the request never gets a response to parse). Verified this is specific
to fred.stlouisfed.org, not a general outbound-network failure: BLS's own
public API (`api.bls.gov`), BEA (`www.bea.gov`), and the Federal Reserve's
own site (`www.federalreserve.gov`) all returned real 200 responses in
under 1.5s in the same test run. This module provides a real, working
fallback for the two FRED series that BLS also publishes (CPI, civilian
unemployment rate); the Fed Funds policy rate has no equivalent free
keyless numeric source, so a FRED failure still leaves `policy_rate`
genuinely unavailable — this module does not fabricate one.

Failure mode: any network error, malformed response, or unparseable JSON
returns None — callers must treat that as "data unavailable", never as
"no evidence exists" (that's a materially different, wrong conclusion)."""

from __future__ import annotations

from datetime import date

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url, get_ssl_context

_BLS_BASE_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Real BLS series ids.
SERIES_CPI_U = "CUUR0000SA0"       # CPI-U, All Items, U.S. city average, not seasonally adjusted
SERIES_UNRATE = "LNS14000000"      # Civilian Unemployment Rate, seasonally adjusted


def _fetch_bls_series(series_id: str, timeout: float = 10.0) -> list[tuple[date, float]] | None:
    """Fetches one BLS series as (date, value) pairs. Returns None on any
    failure — never fabricates data."""
    url = f"{_BLS_BASE_URL}{series_id}"

    try:
        assert_safe_url(url)
    except SSRFError:
        return None

    try:
        response = httpx.get(
            url, timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"},
            verify=get_ssl_context(),
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    if len(response.content) > MAX_RESPONSE_BYTES:
        return None

    return _parse_bls_response(response, series_id)


def _parse_bls_response(response: httpx.Response, series_id: str) -> list[tuple[date, float]] | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    if payload.get("status") != "REQUEST_SUCCEEDED":
        return None

    series_list = payload.get("Results", {}).get("series", [])
    matching = next((s for s in series_list if s.get("seriesID") == series_id), None)
    if matching is None:
        return None

    observations: list[tuple[date, float]] = []
    _MONTH_PERIODS = {f"M{i:02d}": i for i in range(1, 13)}
    for point in matching.get("data", []):
        period = point.get("period", "")
        month = _MONTH_PERIODS.get(period)
        if month is None:  # skip annual/quarterly aggregate rows (M13 etc.)
            continue
        try:
            year = int(point["year"])
            value = float(point["value"])
        except (KeyError, ValueError, TypeError):
            continue
        observations.append((date(year, month, 1), value))

    if not observations:
        return None
    observations.sort(key=lambda pair: pair[0])
    return observations


def fetch_cpi_index_series(timeout: float = 10.0) -> list[tuple[date, float]] | None:
    """Real CPI-U index series from BLS — same underlying concept as
    FRED's CPIAUCSL, real fallback source when FRED is unreachable."""
    return _fetch_bls_series(SERIES_CPI_U, timeout=timeout)


def fetch_unemployment_rate_series(timeout: float = 10.0) -> list[tuple[date, float]] | None:
    """Real civilian unemployment rate series from BLS — same underlying
    concept as FRED's UNRATE."""
    return _fetch_bls_series(SERIES_UNRATE, timeout=timeout)
