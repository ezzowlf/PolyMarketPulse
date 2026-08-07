"""CoinGecko public price provider — free, keyless tier only.

Used by the quant forecasting model (prediction/quant.py) to supply real
current price and realized historical volatility for supported crypto
assets. No API key, no paid endpoints. Network calls go through the same
SSRF guard used elsewhere in this codebase (security.assert_safe_url).

Failure mode: any network error, malformed response, timeout, or
rate limit returns None — callers must treat that as "data unavailable"
and must NOT fabricate a fallback price or volatility."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url

_BASE_URL = "https://api.coingecko.com/api/v3"

# Map our internal asset aliases to CoinGecko coin ids.
_COINGECKO_IDS: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum", "ether": "ethereum",
    "solana": "solana", "sol": "solana",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "xrp": "ripple", "ripple": "ripple",
    "cardano": "cardano", "ada": "cardano",
}


@dataclass(frozen=True)
class PriceData:
    """Real price + realized volatility pulled from CoinGecko's free tier."""

    current_price: float
    # Daily realized volatility (stdev of daily log returns), NOT annualized.
    daily_volatility: float
    days_of_history: int


def resolve_coingecko_id(asset: str | None) -> str | None:
    if not asset:
        return None
    return _COINGECKO_IDS.get(asset.lower())


def fetch_price_and_volatility(
    asset: str, days: int = 90, timeout: float = 10.0
) -> PriceData | None:
    """Fetch current price and realized daily volatility for `asset`.

    `asset` must already be a CoinGecko coin id (use resolve_coingecko_id
    first). Uses the /coins/{id}/market_chart endpoint (free, keyless) to
    get `days` days of daily close prices, then computes current price
    (last close) and the sample stdev of daily log returns.

    Returns None on any failure — never fabricates data."""
    coingecko_id = asset
    url = f"{_BASE_URL}/coins/{coingecko_id}/market_chart?vs_currency=usd&days={days}&interval=daily"

    try:
        assert_safe_url(url)
    except SSRFError:
        return None

    try:
        response = httpx.get(
            url, timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"}
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    if len(response.content) > MAX_RESPONSE_BYTES:
        return None

    try:
        payload = response.json()
        prices = payload.get("prices")
        if not prices or not isinstance(prices, list) or len(prices) < 3:
            return None
        closes = [float(p[1]) for p in prices if isinstance(p, list) and len(p) == 2]
        if len(closes) < 3:
            return None
    except (ValueError, TypeError, KeyError):
        return None

    current_price = closes[-1]
    if current_price <= 0:
        return None

    # Daily log returns
    log_returns = []
    for prev, cur in pairwise(closes):
        if prev <= 0 or cur <= 0:
            continue
        log_returns.append(math.log(cur / prev))

    if len(log_returns) < 2:
        return None

    mean_return = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_return) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_volatility = math.sqrt(variance)

    if daily_volatility <= 0:
        return None

    return PriceData(
        current_price=current_price,
        daily_volatility=daily_volatility,
        days_of_history=len(closes),
    )
