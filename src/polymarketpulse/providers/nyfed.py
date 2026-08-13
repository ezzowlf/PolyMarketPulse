"""New York Fed reference rates provider — free, keyless, official public
API (Federal Reserve Bank of New York), used as the real fallback for the
Fed Funds policy rate specifically, closing the one gap `providers/bls.py`
explicitly documented as unfillable (BLS publishes CPI/unemployment, not
an interest rate).

Root cause this closes: `fetch_macro_snapshot()` returns None whenever
FRED itself is unreachable, even after the BLS fallback fills CPI/
unemployment, because the Fed Funds policy rate had no fallback at all —
verified live this round: with FRED down, `providers.bls` successfully
returns 30 real CPI/unemployment observations each, but the whole
snapshot still comes back None purely because `fedfunds` stays empty.

The NY Fed publishes the real, official Effective Federal Funds Rate
(EFFR) daily via `https://markets.newyorkfed.org/api/rates/all/latest.json`
— no API key, no scraping, an authoritative primary source (the Federal
Reserve Bank of New York is the entity that actually calculates EFFR).
Verified live 2026-08-13: HTTP 200, real JSON, EFFR=3.63% for 2026-08-12
with targetRateFrom/targetRateTo=3.50/3.75 (the real FOMC target range).

Failure mode: any network error, missing EFFR entry, or unparseable JSON
returns None — never a fabricated rate."""

from __future__ import annotations

from datetime import date

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url, get_ssl_context

_API_URL = "https://markets.newyorkfed.org/api/rates/all/latest.json"


def fetch_effr(timeout: float = 10.0) -> list[tuple[date, float]] | None:
    """Real, latest Effective Federal Funds Rate as a single (date, value)
    observation — matches the shape providers/fred.py's fedfunds series
    uses (build_macro_snapshot only ever reads the last element), so this
    is a drop-in fallback, not a parallel data model."""
    try:
        assert_safe_url(_API_URL)
    except SSRFError:
        return None

    try:
        response = httpx.get(
            _API_URL, timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"},
            verify=get_ssl_context(),
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if len(response.content) > MAX_RESPONSE_BYTES:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    entries = payload.get("refRates") or []
    effr_entries = [e for e in entries if e.get("type") == "EFFR"]
    if not effr_entries:
        return None
    latest = effr_entries[0]
    try:
        obs_date = date.fromisoformat(latest["effectiveDate"])
        rate = float(latest["percentRate"])
    except (KeyError, ValueError, TypeError):
        return None
    return [(obs_date, rate)]
