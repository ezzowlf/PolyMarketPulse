"""Pyth Network Hermes price provider — free, keyless (as of this round;
Hermes' public docs state a PYTH_API_KEY becomes required 2026-08-26)
current-price feed for named-contract-month commodity futures.

WTI-golden-case: Polymarket's WTI $85 market (polymarket:3310013) names
Pyth as its primary resolution source, specifically the "Active Month
WTI Crude Oil futures" 1-minute-candle High/Low. This module supplies
only the CURRENT price (Hermes' /v2/updates/price/latest endpoint,
verified live against the real Pyth production API during this round —
see analysis/reports for the exact feed ids and a live sample). It does
NOT supply historical OHLC/candle data: Hermes' public REST API has no
general historical-candle endpoint (confirmed by both its own docs and a
live 404 against /v2/updates/price/{timestamp} for a real past
timestamp during this round's research) — the real resolution text itself
points users to a SEPARATE explorer (pythdata.app) for historical
1-minute candles, not the core Hermes API. Realized volatility and
"August high" therefore remain honestly unavailable from this module
alone; do not fabricate them from a single current-price call.

Failure mode: any network error, malformed response, timeout, or missing
feed returns None — callers must treat that as "data unavailable" and
must NOT fabricate a fallback price."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url, get_ssl_context

_BASE_URL = "https://hermes.pyth.network"

# Real, live-verified Pyth Hermes feed ids for named WTI contract months
# (CME delivery-month codes: F=Jan G=Feb H=Mar J=Apr K=May M=Jun N=Jul
# Q=Aug U=Sep V=Oct X=Nov Z=Dec, year digit = last digit of the year).
# Verified live against https://hermes.pyth.network/v2/price_feeds?query=WTI
# during this round -- an incomplete, hand-curated set (only the contracts
# actually relevant to 2026 WTI touch markets), not the full Pyth catalog.
# Deliberately NOT auto-discovered at runtime: the /v2/price_feeds search
# endpoint is a convenience/discovery tool, not something this app should
# depend on at forecast time (a typo'd search term could silently resolve
# to the wrong contract).
WTI_CONTRACT_FEED_IDS: dict[str, str] = {
    "WTIU6": "17d0b3b03f9ccb6bb6721960f034b8601b3d89ef70743b33f86304a1565cebda",  # Sep 2026 delivery
    "WTIV6": "9526e04755ebaed86733913b84fe14db4ea165da8f40f97710014cd877fe545b",  # Oct 2026 delivery
    "WTIX6": "f8c2191e76f7f4d5335e7f4e8f81ab0df6360d54ee020874222841894203e9d7",  # Nov 2026 delivery
}


@dataclass(frozen=True)
class PythPrice:
    """One real, live Hermes price observation for a named contract."""

    feed_id: str
    price: float
    confidence: float
    publish_time: datetime  # UTC, from Hermes' own publish_time (real exchange-side timestamp)
    retrieved_at: datetime  # UTC, when THIS app made the call (point-in-time honesty)

    def as_dict(self) -> dict:
        return {
            "feed_id": self.feed_id, "price": self.price, "confidence": self.confidence,
            "publish_time": self.publish_time.isoformat(), "retrieved_at": self.retrieved_at.isoformat(),
        }


def fetch_latest_price(feed_id: str, timeout: float = 10.0) -> PythPrice | None:
    """Fetch the current, real price for one Pyth feed id via Hermes'
    /v2/updates/price/latest endpoint. Returns None on any failure --
    never fabricates a price."""
    url = f"{_BASE_URL}/v2/updates/price/latest?ids[]={feed_id}&parsed=true"
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

    try:
        payload = response.json()
        parsed = payload.get("parsed")
        if not parsed or not isinstance(parsed, list):
            return None
        entry = parsed[0]
        price_block = entry["price"]
        raw_price = int(price_block["price"])
        raw_conf = int(price_block["conf"])
        expo = int(price_block["expo"])
        publish_time_unix = int(price_block["publish_time"])
    except (ValueError, TypeError, KeyError, IndexError):
        return None

    price = raw_price * (10**expo)
    confidence = raw_conf * (10**expo)
    if price <= 0:
        return None

    return PythPrice(
        feed_id=feed_id,
        price=price,
        confidence=confidence,
        publish_time=datetime.fromtimestamp(publish_time_unix, tz=UTC),
        retrieved_at=datetime.now(UTC),
    )


# --- Active Month resolution (Block D) --------------------------------
#
# Implements the exact roll rule from polymarket:3310013's real resolution
# text: "Per CME contract specifications for WTI Crude Oil (CL) futures, a
# contract's last trading day is three business days prior to the 25th
# calendar day of the month preceding the contract's delivery month (or
# four business days prior if the 25th calendar day is not a business
# day)... The active month changes at the start of the second trading
# session prior to the nearest listed contract's last trading session."
#
# Documented limitation: "business day" here means Mon-Fri only -- this
# does NOT consult a real CME/NYSE holiday calendar, so a last-trading-day
# or roll-date that happens to fall on a US market holiday will be off by
# one real trading session. Never silently treated as exact; every caller
# gets this same caveat via the docstrings below.
#
# Verified against the resolution text's own worked example ("if the 25th
# is a Saturday, LTD = Tuesday the 21st, roll date = Friday the 17th") and
# cross-checked live against Pyth's own feed metadata during this round:
# WTIU6 (September 2026 delivery)'s computed last_trading_day here is
# 2026-08-20, matching Pyth's own feed description "PYTH WTI 20 AUGUST
# 2026" for that exact feed id.

_MONTH_CODES = "FGHJKMNQUVXZ"  # CME delivery-month codes, Jan..Dec


def _business_days_before(anchor: date, n: int) -> date:
    """The date that is `n` business days (Mon-Fri) strictly before
    `anchor`. No holiday calendar -- see module docstring."""
    cur = anchor
    count = 0
    while count < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            count += 1
    return cur


def last_trading_day(delivery_year: int, delivery_month: int) -> date:
    """The real CME rule: 3 (or 4, if the 25th isn't a business day)
    business days before the 25th of the month preceding delivery_month."""
    prev_month = delivery_month - 1
    prev_year = delivery_year
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    the_25th = date(prev_year, prev_month, 25)
    n = 3 if the_25th.weekday() < 5 else 4
    return _business_days_before(the_25th, n)


def _roll_date(delivery_year: int, delivery_month: int) -> date:
    """When THIS contract stops being the active month (2 business days
    before its own last trading day) -- the point at which the NEXT
    contract (delivery_month + 1) takes over."""
    return _business_days_before(last_trading_day(delivery_year, delivery_month), 2)


def _add_month(year: int, month: int) -> tuple[int, int]:
    month += 1
    if month > 12:
        return year + 1, 1
    return year, month


def active_month_contract(as_of: date) -> tuple[int, int]:
    """The (year, month) of the real Active Month WTI contract as of
    `as_of`, per the exact roll rule above. This is a pure function of the
    calendar -- no network call, no market-specific state."""
    year, month = as_of.year, as_of.month
    # Step 1: find the "nearest listed contract" -- the earliest delivery
    # month whose own last trading day has not yet passed.
    for _ in range(12):
        if last_trading_day(year, month) >= as_of:
            break
        year, month = _add_month(year, month)
    else:  # pragma: no cover - defensive only, cannot occur with a sane as_of
        raise ValueError(f"no unexpired WTI contract found within 12 months of {as_of}")
    # Step 2: if we're within that contract's final ~3 sessions (at or
    # after its roll date), the NEXT contract is already the active month.
    if as_of >= _roll_date(year, month):
        year, month = _add_month(year, month)
    return year, month


def active_month_symbol(as_of: date) -> str:
    """The Pyth-style contract symbol (e.g. "WTIU6") for the active month
    as of `as_of`. `year % 10` matches Pyth's single-digit year convention
    (confirmed live: WTIU6 = September 2026)."""
    year, month = active_month_contract(as_of)
    return f"WTI{_MONTH_CODES[month - 1]}{year % 10}"
