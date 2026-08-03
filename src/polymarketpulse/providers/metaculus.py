from __future__ import annotations

from typing import Any

from ..models import Market
from .base import Page, PredictionMarketProvider, ProviderCapabilities


class MetaculusProvider(PredictionMarketProvider):
    """Placeholder for Metaculus (metaculus.com).

    Metaculus is a forecasting platform (community probability estimates,
    not a market with prices/liquidity in the trading sense). Its legacy
    `api2/questions/` endpoint returned 403 without browser-session
    authentication when checked during this build, and the newer API
    requires further review of terms of use before scraping question data
    at scale. Left unimplemented rather than guessing at auth requirements.
    """

    name = "metaculus"
    capabilities = ProviderCapabilities(
        market_lists=False,
        prices=False,
        orderbook=False,
        volume=False,
        liquidity=False,
        resolution=False,
        requires_auth=True,
        real_money=False,
        notes=(
            "Not implemented: public API endpoint returned 403 during "
            "evaluation; needs confirmed auth/rate-limit requirements "
            "before wiring up (https://www.metaculus.com/api/)."
        ),
    )

    def fetch_markets(self, limit: int = 100, cursor: str | None = None, page_size: int = 100) -> Page[Market]:
        raise NotImplementedError(self.capabilities.notes)

    def fetch_resolved_markets(self, limit: int = 100, cursor: str | None = None) -> Page[Market]:
        raise NotImplementedError(self.capabilities.notes)

    def normalize_market(self, raw: dict[str, Any]) -> Market:
        raise NotImplementedError(self.capabilities.notes)
