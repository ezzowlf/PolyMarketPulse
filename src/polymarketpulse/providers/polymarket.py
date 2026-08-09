from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import httpx

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - truststore is a declared dependency
    pass

from ..models import Market, ResolutionStatus
from ..security import get_ssl_context
from .base import (
    Page,
    PredictionMarketProvider,
    ProviderCapabilities,
    ProviderConnectionError,
    ProviderResponseError,
    ProviderTimeoutError,
)

try:
    from .. import data_sources
except ImportError:
    data_sources = None

GAMMA_URL = "https://gamma-api.polymarket.com"

# Fields checked for presence/quality. Missing ones are recorded on the
# normalized market instead of causing a crash.
_TRACKED_FIELDS = (
    "id",
    "conditionId",
    "question",
    "slug",
    "endDate",
    "liquidityNum",
    "volume24hr",
    "outcomePrices",
    "clobTokenIds",
    "bestBid",
    "bestAsk",
    "spread",
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _category(raw: dict[str, Any]) -> str | None:
    events = raw.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict) and first.get("title"):
            return str(first["title"])
    return None


def _event_id(raw: dict[str, Any]) -> str | None:
    events = raw.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    return None


def _tags(raw: dict[str, Any]) -> tuple[str, ...]:
    events = raw.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict) and isinstance(first.get("tags"), list):
            names = [t.get("label") if isinstance(t, dict) else t for t in first["tags"]]
            return tuple(str(n) for n in names if n)
    return ()


def _raw_hash(raw: dict[str, Any]) -> str:
    try:
        payload = json.dumps(raw, sort_keys=True, default=str)
    except TypeError:
        payload = str(raw)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolution(raw: dict[str, Any], outcomes: list[str], prices: dict[str, float]) -> tuple:
    """Best-effort resolution status derived from `closed` + outcome prices
    + UMA proposer status. Polymarket doesn't expose a single explicit
    "resolved" boolean on this endpoint; a closed market whose outcome
    prices have collapsed to (0, 1) is treated as resolved, with the
    1-priced outcome as the winner.
    """
    closed = bool(raw.get("closed"))
    uma_status = _array(raw.get("umaResolutionStatuses"))
    status_hint = str(uma_status[0]).lower() if uma_status else None
    archived = bool(raw.get("archived"))

    if not closed:
        return ResolutionStatus.UNRESOLVED, None, None

    if status_hint == "disputed":
        return ResolutionStatus.DISPUTED, None, status_hint

    winning_outcome = None
    for outcome in outcomes:
        price = prices.get(outcome.lower())
        if price is not None and price >= 0.99:
            winning_outcome = outcome
            break

    if winning_outcome is not None:
        return ResolutionStatus.RESOLVED, winning_outcome, status_hint

    if archived and not prices:
        return ResolutionStatus.CANCELLED, None, status_hint

    all_near_half = bool(prices) and all(0.4 <= p <= 0.6 for p in prices.values())
    if closed and all_near_half:
        return ResolutionStatus.INVALID, None, status_hint

    if status_hint == "proposed":
        return ResolutionStatus.PROPOSED, None, status_hint
    return ResolutionStatus.UNKNOWN, None, status_hint


def parse_market(raw: dict[str, Any]) -> Market:
    outcomes = [str(o) for o in _array(raw.get("outcomes"))]
    prices = _array(raw.get("outcomePrices"))
    mapped: dict[str, float] = {}
    price_tuple: list[float | None] = []
    for outcome, price in zip(outcomes, prices, strict=False):
        parsed_price = _optional_number(price)
        price_tuple.append(parsed_price)
        if parsed_price is not None:
            mapped[outcome.lower()] = parsed_price

    token_ids = _array(raw.get("clobTokenIds"))
    yes_token = str(token_ids[0]) if len(token_ids) > 0 else None
    no_token = str(token_ids[1]) if len(token_ids) > 1 else None

    slug = str(raw.get("slug") or "")
    missing = tuple(field for field in _TRACKED_FIELDS if raw.get(field) in (None, "", []))

    resolution_status, winning_outcome, resolution_source = _resolution(raw, outcomes, mapped)
    _terminal_statuses = (
        ResolutionStatus.RESOLVED,
        ResolutionStatus.CANCELLED,
        ResolutionStatus.INVALID,
        ResolutionStatus.DISPUTED,
    )
    resolved_at = _date(raw.get("updatedAt")) if resolution_status in _terminal_statuses else None

    return Market(
        provider="polymarket",
        provider_market_id=str(raw.get("id") or raw.get("conditionId") or slug),
        condition_id=str(raw.get("conditionId") or ""),
        question=str(raw.get("question") or raw.get("title") or "Unbenannter Markt"),
        slug=slug,
        description=str(raw.get("description")) if raw.get("description") else None,
        event_id=_event_id(raw),
        category=_category(raw),
        tags=_tags(raw),
        outcomes=tuple(outcomes),
        outcome_prices=tuple(price_tuple),
        yes_price=mapped.get("yes"),
        no_price=mapped.get("no"),
        yes_token_id=yes_token,
        no_token_id=no_token,
        best_bid=_optional_number(raw.get("bestBid")),
        best_ask=_optional_number(raw.get("bestAsk")),
        liquidity=_number(raw.get("liquidityNum", raw.get("liquidity"))),
        volume_24h=_number(raw.get("volume24hr", raw.get("volume24h"))),
        volume_total=_number(raw.get("volumeNum", raw.get("volume"))),
        spread=_optional_number(raw.get("spread")),
        one_day_change=_optional_number(raw.get("oneDayPriceChange")),
        created_at=_date(raw.get("createdAt")),
        start_at=_date(raw.get("startDate")),
        end_at=_date(raw.get("endDate") or raw.get("end_date_iso")),
        updated_at=_date(raw.get("updatedAt")),
        resolved_at=resolved_at,
        resolution_status=resolution_status,
        winning_outcome=winning_outcome,
        resolution_source=resolution_source,
        url=f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
        missing_fields=missing,
        provider_data={
            k: raw.get(k)
            for k in ("negRisk", "enableOrderBook", "restricted", "featured", "new")
            if k in raw
        },
        raw_data_hash=_raw_hash(raw),
    )


class PolymarketProvider(PredictionMarketProvider):
    """Read-only adapter around the public Polymarket Gamma API.

    No wallet, no private key, no order placement of any kind.
    """

    name = "polymarket"
    capabilities = ProviderCapabilities(
        market_lists=True,
        prices=True,
        orderbook=False,  # CLOB order book not wired up in this MVP
        volume=True,
        liquidity=True,
        resolution=True,
        requires_auth=False,
        real_money=True,
        notes="Public Gamma API only (no wallet/order endpoints used).",
    )

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"},
            verify=get_ssl_context(),
        )
        self._health: data_sources.ProviderHealth | None = None

    def close(self) -> None:
        self._client.close()

    def get_health(self) -> data_sources.ProviderHealth | None:
        """Return the last fetch health metrics, if available."""
        return self._health

    def set_health(self, health: data_sources.ProviderHealth) -> None:
        """Set health metrics (typically called by the scanner after a fetch)."""
        self._health = health

    def normalize_market(self, raw: dict[str, Any]) -> Market:
        return parse_market(raw)

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.get(f"{GAMMA_URL}/markets/keyset", params=params)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(
                "Could not reach the Polymarket API. Check your internet connection."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Polymarket API request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderResponseError(
                f"Polymarket API returned an error: {exc.response.status_code}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Polymarket API returned invalid JSON.") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
            raise ProviderResponseError("Unexpected Polymarket API response shape.")
        return payload

    def _fetch_paginated(
        self, base_params: dict[str, Any], limit: int, cursor: str | None, page_size: int
    ) -> Page[Market]:
        markets: list[Market] = []
        page_size = max(1, min(page_size, limit))
        next_cursor = cursor
        while len(markets) < limit:
            remaining = limit - len(markets)
            batch_limit = min(page_size, remaining)
            params = dict(base_params, limit=batch_limit)
            if next_cursor:
                params["cursor"] = next_cursor

            payload = self._request(params)
            raw_markets = payload["markets"]
            markets.extend(parse_market(item) for item in raw_markets if isinstance(item, dict))

            next_cursor = payload.get("next_cursor")
            if not next_cursor or not raw_markets:
                break

        return Page(items=markets[:limit], next_cursor=next_cursor if len(markets) >= limit else None)

    def fetch_markets(
        self, limit: int = 100, cursor: str | None = None, page_size: int = 100
    ) -> Page[Market]:
        return self._fetch_paginated(
            {
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
            },
            limit=limit,
            cursor=cursor,
            page_size=page_size,
        )

    def fetch_resolved_markets(self, limit: int = 100, cursor: str | None = None) -> Page[Market]:
        return self._fetch_paginated(
            {
                "closed": "true",
                "order": "endDate",
                "ascending": "false",
            },
            limit=limit,
            cursor=cursor,
            page_size=min(100, limit),
        )
