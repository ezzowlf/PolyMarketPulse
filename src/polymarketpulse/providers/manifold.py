from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

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

API_URL = "https://api.manifold.markets/v0"


def _ms_to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def parse_market(raw: dict[str, Any]) -> Market:
    outcome_type = raw.get("outcomeType")
    pool = raw.get("pool") if isinstance(raw.get("pool"), dict) else {}
    probability = raw.get("probability")

    yes_price = float(probability) if outcome_type == "BINARY" and probability is not None else None
    no_price = 1 - yes_price if yes_price is not None else None

    is_resolved = bool(raw.get("isResolved"))
    resolution = str(raw.get("resolution")) if raw.get("resolution") is not None else None
    resolution_status = ResolutionStatus.RESOLVED if is_resolved else ResolutionStatus.UNRESOLVED
    resolved_at = _ms_to_datetime(raw.get("resolutionTime")) if is_resolved else None

    missing = tuple(
        field
        for field in ("probability", "volume", "totalLiquidity", "closeTime")
        if raw.get(field) in (None, "")
    )

    return Market(
        provider="manifold",
        provider_market_id=str(raw.get("id") or ""),
        condition_id=str(raw.get("id") or ""),
        question=str(raw.get("question") or "Unbenannter Markt"),
        slug=str(raw.get("slug") or ""),
        description=None,
        category=str(raw.get("groupSlugs")[0]) if raw.get("groupSlugs") else None,
        tags=tuple(raw.get("groupSlugs") or ()),
        outcomes=("YES", "NO") if outcome_type == "BINARY" else (),
        outcome_prices=(yes_price, no_price) if yes_price is not None else (),
        yes_price=yes_price,
        no_price=no_price,
        liquidity=float(raw.get("totalLiquidity") or 0.0),
        volume_24h=float(raw.get("volume24Hours") or 0.0),
        volume_total=float(raw.get("volume") or 0.0),
        created_at=_ms_to_datetime(raw.get("createdTime")),
        end_at=_ms_to_datetime(raw.get("closeTime")),
        updated_at=_ms_to_datetime(raw.get("lastUpdatedTime")),
        resolved_at=resolved_at,
        resolution_status=resolution_status,
        winning_outcome=resolution,
        resolution_source="manifold" if is_resolved else None,
        url=str(raw.get("url") or "https://manifold.markets"),
        missing_fields=missing,
        provider_data={"mechanism": raw.get("mechanism"), "pool": pool},
    )


class ManifoldProvider(PredictionMarketProvider):
    """Read-only adapter around the public Manifold Markets API.

    Manifold is play-money (no real funds change hands) and its read API
    requires no authentication, so this adapter is fully functional.
    """

    name = "manifold"
    capabilities = ProviderCapabilities(
        market_lists=True,
        prices=True,
        orderbook=False,
        volume=True,
        liquidity=True,
        resolution=True,
        requires_auth=False,
        real_money=False,
        notes="Public, unauthenticated API. Play-money exchange.",
    )

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"},
            verify=get_ssl_context(),
        )

    def close(self) -> None:
        self._client.close()

    def normalize_market(self, raw: dict[str, Any]) -> Market:
        return parse_market(raw)

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            response = self._client.get(f"{API_URL}{path}", params=params)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise ProviderConnectionError(
                "Could not reach the Manifold API. Check your internet connection."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Manifold API request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderResponseError(f"Manifold API returned an error: {exc.response.status_code}") from exc
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderResponseError("Manifold API returned invalid JSON.") from exc

    def fetch_markets(
        self, limit: int = 100, cursor: str | None = None, page_size: int = 100
    ) -> Page[Market]:
        markets: list[Market] = []
        before = cursor
        page_size = max(1, min(page_size, limit))
        while len(markets) < limit:
            batch_limit = min(page_size, limit - len(markets))
            params: dict[str, Any] = {"limit": batch_limit}
            if before:
                params["before"] = before
            payload = self._get("/markets", params)
            if not isinstance(payload, list):
                raise ProviderResponseError("Unexpected Manifold API response shape (expected a list).")
            active = [
                item
                for item in payload
                if isinstance(item, dict) and not item.get("isResolved") and not item.get("closeTime", 1) < 0
            ]
            markets.extend(parse_market(item) for item in active)
            if len(payload) < batch_limit:
                before = None
                break
            before = payload[-1].get("id")
        return Page(items=markets[:limit], next_cursor=before)

    def fetch_resolved_markets(self, limit: int = 100, cursor: str | None = None) -> Page[Market]:
        # Manifold has no dedicated "resolved only" listing endpoint; page
        # through recent markets and keep the resolved ones.
        markets: list[Market] = []
        before = cursor
        scanned = 0
        max_scan = limit * 10  # bounded scan so an all-unresolved page can't loop forever
        while len(markets) < limit and scanned < max_scan:
            params: dict[str, Any] = {"limit": min(100, max_scan - scanned)}
            if before:
                params["before"] = before
            payload = self._get("/markets", params)
            if not isinstance(payload, list) or not payload:
                break
            scanned += len(payload)
            markets.extend(
                parse_market(item)
                for item in payload
                if isinstance(item, dict) and item.get("isResolved")
            )
            before = payload[-1].get("id")
        return Page(items=markets[:limit], next_cursor=before)
