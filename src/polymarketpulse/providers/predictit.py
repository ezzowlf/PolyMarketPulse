from __future__ import annotations

from typing import Any

import httpx

from ..models import Market, ResolutionStatus
from .base import (
    Page,
    PredictionMarketProvider,
    ProviderCapabilities,
    ProviderConnectionError,
    ProviderResponseError,
    ProviderTimeoutError,
)

API_URL = "https://www.predictit.org/api/marketdata/all/"


def _parse_contract(market_raw: dict[str, Any], contract: dict[str, Any]) -> Market:
    status = str(contract.get("status") or "")
    is_open = status.lower() == "open"
    last_price = contract.get("lastTradePrice")
    yes_price = float(last_price) if isinstance(last_price, int | float) else None

    missing = tuple(
        field
        for field in ("lastTradePrice", "bestBuyYesCost", "bestSellYesCost")
        if contract.get(field) in (None, "")
    )

    provider_market_id = f"{market_raw.get('id')}:{contract.get('id')}"
    resolution_status = ResolutionStatus.UNRESOLVED if is_open else ResolutionStatus.UNKNOWN

    return Market(
        provider="predictit",
        provider_market_id=provider_market_id,
        condition_id=str(contract.get("id") or ""),
        question=f"{market_raw.get('shortName') or market_raw.get('name')} — {contract.get('shortName') or contract.get('name')}",
        slug=str(market_raw.get("id") or ""),
        description=str(market_raw.get("name") or "") or None,
        event_id=str(market_raw.get("id") or ""),
        outcomes=("Yes", "No"),
        outcome_prices=(yes_price, (1 - yes_price) if yes_price is not None else None),
        yes_price=yes_price,
        no_price=(1 - yes_price) if yes_price is not None else None,
        best_bid=_num(contract.get("bestBuyNoCost")),
        best_ask=_num(contract.get("bestBuyYesCost")),
        # PredictIt's public feed does not expose volume or liquidity figures.
        liquidity=0.0,
        volume_24h=0.0,
        volume_total=0.0,
        resolution_status=resolution_status,
        url=str(market_raw.get("url") or "https://www.predictit.org"),
        missing_fields=missing,
        provider_data={"contract_status": status, "market_id": market_raw.get("id")},
    )


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class PredictItProvider(PredictionMarketProvider):
    """Read-only adapter around PredictIt's public `marketdata/all` feed.

    PredictIt is a real-money, CFTC no-action-letter exchange. Each
    PredictIt "market" (e.g. an election) contains multiple binary
    contracts (e.g. individual candidates); each contract is normalized
    into its own `Market` row here since that's the level prices/outcomes
    exist at. The public feed has no volume or liquidity figures, and no
    authentication is used or required for this read-only endpoint.
    """

    name = "predictit"
    capabilities = ProviderCapabilities(
        market_lists=True,
        prices=True,
        orderbook=False,
        volume=False,
        liquidity=False,
        resolution=False,
        requires_auth=False,
        real_money=True,
        notes=(
            "Public unauthenticated feed; no volume/liquidity data and no "
            "distinct resolved-markets endpoint are exposed."
        ),
    )

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.Client(timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"})
        self._cache: list[dict[str, Any]] | None = None

    def close(self) -> None:
        self._client.close()

    def normalize_market(self, raw: dict[str, Any]) -> Market:
        contracts = raw.get("contracts") or [{}]
        return _parse_contract(raw, contracts[0])

    def _fetch_all(self) -> list[dict[str, Any]]:
        if self._cache is not None:
            return self._cache
        try:
            response = self._client.get(API_URL)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(
                "Could not reach the PredictIt API. Check your internet connection."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("PredictIt API request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderResponseError(f"PredictIt API returned an error: {exc.response.status_code}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError("PredictIt API returned invalid JSON.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
            raise ProviderResponseError("Unexpected PredictIt API response shape.")
        self._cache = payload["markets"]
        return self._cache

    def fetch_markets(
        self, limit: int = 100, cursor: str | None = None, page_size: int = 100
    ) -> Page[Market]:
        # PredictIt returns the full market set in a single response; we
        # slice it manually for a consistent limit/cursor interface.
        all_markets = self._fetch_all()
        offset = int(cursor) if cursor else 0
        contracts: list[Market] = []
        for market_raw in all_markets[offset:]:
            for contract in market_raw.get("contracts") or []:
                if str(contract.get("status", "")).lower() == "open":
                    contracts.append(_parse_contract(market_raw, contract))
                if len(contracts) >= limit:
                    break
            if len(contracts) >= limit:
                break
        next_cursor = str(offset + limit) if offset + limit < len(all_markets) else None
        return Page(items=contracts[:limit], next_cursor=next_cursor)

    def fetch_resolved_markets(self, limit: int = 100, cursor: str | None = None) -> Page[Market]:
        # Not exposed by the public feed (closed markets drop out of it).
        raise NotImplementedError(
            "PredictIt's public feed only lists currently-open markets; "
            "resolved-market history is not available without authenticated access."
        )
