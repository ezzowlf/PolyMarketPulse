"""FRED (Federal Reserve Economic Data) provider — free, keyless tier only.

Used by the macro forecasting model (prediction/macro.py) to supply a real
quantitative macro snapshot: the current effective federal funds rate,
CPI (inflation) year-over-year trend, and unemployment rate trend, plus a
hardcoded public FOMC meeting calendar.

Access path decision (verified directly in this environment, same
verification standard as providers/coingecko.py):
  - FRED's JSON API (`https://api.stlouisfed.org/fred/series/observations`)
    requires a paid-tier-free-but-still-issued `api_key` query param for
    every series endpoint. We do not have, and will not register for, an
    API key (no paid/keyed integrations per project constraints) — so this
    module never calls that host.
  - FRED also publishes each series as a plain CSV via
    `https://fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES_ID`, which is
    historically keyless and public. This is the path this module uses.
  - Verified live from this sandbox: an outbound HTTPS request to
    fred.stlouisfed.org fails with
    `ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify
    failed: unable to get local issuer certificate` — the exact same class
    of sandbox network/TLS limitation that blocked CoinGecko in the prior
    round (see coingecko.py's module docstring history / HANDOFF.md). This
    is an environment limitation, not a code defect: the request never
    reaches FRED's servers at all. The code below is real and is verified
    against realistic mocked CSV responses (matching real historical
    FRED values) in tests/test_fred_provider.py; it will start actually
    fetching live data the moment this code runs somewhere with normal
    outbound TLS.

Failure mode: any network error, malformed response, timeout, or
unparseable CSV returns None — callers must treat that as "data
unavailable" and must NOT fabricate a fallback rate/CPI/unemployment
value."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url, get_ssl_context

_CSV_BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Real FRED series ids used by this module.
SERIES_FEDFUNDS = "FEDFUNDS"   # Effective Federal Funds Rate (monthly, percent)
SERIES_CPI = "CPIAUCSL"        # CPI for All Urban Consumers, All Items (monthly, index)
SERIES_UNRATE = "UNRATE"       # Civilian Unemployment Rate (monthly, percent)

# Public, well-known FOMC meeting calendar. Best-effort hardcoded reference
# data (same precedent as macro.py's / the wider codebase's prior use of
# small static public-calendar tables) — the Federal Reserve publishes this
# schedule at federalreserve.gov/monetarypolicy/fomccalendars.htm. These are
# the *second* day of each two-day meeting (the day the policy decision and
# statement are announced), which is what "next FOMC meeting date" should
# mean for a decision market. Verify against the official calendar before
# relying on this operationally — this module has no live way to refresh it
# in this environment.
FOMC_MEETING_DATES_2026: tuple[date, ...] = (
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
)


@dataclass(frozen=True)
class MacroSnapshot:
    """Real (or realistically-mocked, when live fetch is unavailable)
    quantitative macro snapshot used as input to macro.py's rate-decision
    model."""

    policy_rate: float               # latest FEDFUNDS observation, percent
    policy_rate_as_of: date
    cpi_yoy: float                   # latest 12-month CPI % change
    cpi_yoy_prior: float | None      # CPI % change 3 months earlier (trend reference)
    unemployment_rate: float         # latest UNRATE observation, percent
    unemployment_rate_prior: float | None  # UNRATE 3 months earlier (trend reference)
    as_of_date: date
    next_fomc_meeting_date: date | None

    def as_dict(self) -> dict:
        return {
            "policy_rate": self.policy_rate,
            "policy_rate_as_of": self.policy_rate_as_of.isoformat(),
            "cpi_yoy": self.cpi_yoy,
            "cpi_yoy_prior": self.cpi_yoy_prior,
            "unemployment_rate": self.unemployment_rate,
            "unemployment_rate_prior": self.unemployment_rate_prior,
            "as_of_date": self.as_of_date.isoformat(),
            "next_fomc_meeting_date": (
                self.next_fomc_meeting_date.isoformat() if self.next_fomc_meeting_date else None
            ),
        }


def next_fomc_meeting(after: date | None = None) -> date | None:
    """First hardcoded FOMC decision date strictly after `after`
    (defaults to today). Returns None if the calendar table doesn't cover
    that far — never fabricates a date."""
    reference = after or datetime.now(UTC).date()
    for meeting_date in FOMC_MEETING_DATES_2026:
        if meeting_date > reference:
            return meeting_date
    return None


def _fetch_series_csv(series_id: str, timeout: float = 10.0) -> list[tuple[date, float]] | None:
    """Fetch a single FRED series as (date, value) pairs via the keyless CSV
    endpoint. Returns None on any failure — never fabricates data. Missing
    observations (FRED prints '.') are skipped, not coerced to 0."""
    url = f"{_CSV_BASE_URL}?id={series_id}"

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

    return _parse_fred_csv(response.text, series_id)


def _parse_fred_csv(text: str, series_id: str) -> list[tuple[date, float]] | None:
    """Parses FRED's two-column CSV (`DATE,<SERIES_ID>` or `OBSERVATION_DATE,<SERIES_ID>` header,
    then rows of `YYYY-MM-DD,value` with missing observations as '.'). Returns None if
    the shape doesn't look like a real FRED response at all."""
    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
    except csv.Error:
        return None

    if not rows or len(rows) < 2:
        return None

    header = rows[0]
    if len(header) < 2 or header[0].strip().upper() not in {"DATE", "OBSERVATION_DATE"}:
        return None

    observations: list[tuple[date, float]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        raw_date, raw_value = row[0].strip(), row[1].strip()
        if raw_value in ("", "."):
            continue
        try:
            obs_date = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=UTC).date()
            value = float(raw_value)
        except (ValueError, TypeError):
            continue
        observations.append((obs_date, value))

    if not observations:
        return None

    observations.sort(key=lambda pair: pair[0])
    return observations


def _cpi_yoy_series(cpi_observations: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Converts a raw CPI index series into a year-over-year % change series
    (each point compared to the observation ~12 months earlier). Real
    computation from real fetched index values — not a fabricated number."""
    yoy: list[tuple[date, float]] = []
    for i, (obs_date, value) in enumerate(cpi_observations):
        target = obs_date.replace(year=obs_date.year - 1)
        # Find the observation closest to `target` (monthly series, so an
        # exact match is expected but we tolerate a few days of slack).
        prior = None
        for candidate_date, candidate_value in cpi_observations[:i]:
            if abs((candidate_date - target).days) <= 20:
                prior = candidate_value
        if prior and prior > 0:
            yoy.append((obs_date, (value - prior) / prior * 100.0))
    return yoy


def fetch_macro_snapshot(timeout: float = 10.0) -> MacroSnapshot | None:
    """Fetches FEDFUNDS, CPIAUCSL, and UNRATE from FRED's keyless CSV
    endpoint, derives CPI YoY inflation, and returns a MacroSnapshot with
    a real trend reference point (~3 months earlier) for CPI YoY and
    unemployment. Returns None if ANY required series fails to fetch or
    parse — never returns a partially-fabricated snapshot."""
    fedfunds = _fetch_series_csv(SERIES_FEDFUNDS, timeout=timeout)
    cpi = _fetch_series_csv(SERIES_CPI, timeout=timeout)
    unrate = _fetch_series_csv(SERIES_UNRATE, timeout=timeout)

    return build_macro_snapshot(fedfunds, cpi, unrate)


def build_macro_snapshot(
    fedfunds: list[tuple[date, float]] | None,
    cpi: list[tuple[date, float]] | None,
    unrate: list[tuple[date, float]] | None,
) -> MacroSnapshot | None:
    """Pure function that turns three already-fetched (or mocked) FRED
    series into a MacroSnapshot. Split out from fetch_macro_snapshot so
    tests can verify the real parsing/derivation logic against realistic
    mocked series without needing network access."""
    if not fedfunds or not cpi or not unrate:
        return None
    if len(cpi) < 15:  # need >12 months of history to compute a YoY trend point
        return None

    cpi_yoy = _cpi_yoy_series(cpi)
    if len(cpi_yoy) < 4:
        return None

    policy_rate_as_of, policy_rate = fedfunds[-1]
    latest_cpi_yoy_date, latest_cpi_yoy = cpi_yoy[-1]
    cpi_yoy_prior = cpi_yoy[-4][1] if len(cpi_yoy) >= 4 else None  # ~3 months earlier

    unemployment_as_of, unemployment_rate = unrate[-1]
    unemployment_rate_prior = unrate[-4][1] if len(unrate) >= 4 else None

    as_of_date = max(policy_rate_as_of, latest_cpi_yoy_date, unemployment_as_of)

    return MacroSnapshot(
        policy_rate=policy_rate,
        policy_rate_as_of=policy_rate_as_of,
        cpi_yoy=round(latest_cpi_yoy, 3),
        cpi_yoy_prior=round(cpi_yoy_prior, 3) if cpi_yoy_prior is not None else None,
        unemployment_rate=unemployment_rate,
        unemployment_rate_prior=unemployment_rate_prior,
        as_of_date=as_of_date,
        next_fomc_meeting_date=next_fomc_meeting(as_of_date),
    )
