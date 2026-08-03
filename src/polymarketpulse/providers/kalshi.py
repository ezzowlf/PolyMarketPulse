from __future__ import annotations

from typing import Any

from ..models import Market
from .base import Page, PredictionMarketProvider, ProviderCapabilities


class KalshiProvider(PredictionMarketProvider):
    """Placeholder for Kalshi (kalshi.com).

    Kalshi's public read-only market data API requires an authenticated
    session (API key) even for read access to most endpoints, and it is a
    real-money, CFTC-regulated exchange. Implementing this fully means
    deciding on a credential story first — out of scope for this MVP.

    Capabilities are declared honestly (all False except the auth/real-money
    flags) so callers can detect "not usable yet" without a stack trace.
    """

    name = "kalshi"
    capabilities = ProviderCapabilities(
        market_lists=False,
        prices=False,
        orderbook=False,
        volume=False,
        liquidity=False,
        resolution=False,
        requires_auth=True,
        real_money=True,
        notes=(
            "Not implemented: Kalshi's market data API requires an API key "
            "(https://trading-api.readme.io/reference). Add credential "
            "handling and rate-limit-aware pagination before enabling."
        ),
    )

    def fetch_markets(self, limit: int = 100, cursor: str | None = None, page_size: int = 100) -> Page[Market]:
        raise NotImplementedError(self.capabilities.notes)

    def fetch_resolved_markets(self, limit: int = 100, cursor: str | None = None) -> Page[Market]:
        raise NotImplementedError(self.capabilities.notes)

    def normalize_market(self, raw: dict[str, Any]) -> Market:
        raise NotImplementedError(self.capabilities.notes)
