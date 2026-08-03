from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

from ..models import Market

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """A single page of results plus an opaque cursor for the next page."""

    items: list[T]
    next_cursor: str | None = None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    provider: str
    provider_market_id: str
    captured_at: datetime
    bids: tuple[OrderBookLevel, ...] = field(default_factory=tuple)
    asks: tuple[OrderBookLevel, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Self-declared feature matrix for a provider. Used to drive the CLI's
    `providers` / `provider-info` output and to let callers branch on what a
    provider can actually do instead of guessing from exceptions."""

    market_lists: bool = False
    prices: bool = False
    orderbook: bool = False
    volume: bool = False
    liquidity: bool = False
    resolution: bool = False
    requires_auth: bool = False
    real_money: bool = True
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_lists": self.market_lists,
            "prices": self.prices,
            "orderbook": self.orderbook,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "resolution": self.resolution,
            "requires_auth": self.requires_auth,
            "real_money": self.real_money,
            "notes": self.notes,
        }


class ProviderError(RuntimeError):
    """Base class for provider-level failures. Subclasses map onto CLI exit
    codes so callers never need to inspect raw httpx exceptions."""


class ProviderConnectionError(ProviderError):
    pass


class ProviderTimeoutError(ProviderError):
    pass


class ProviderResponseError(ProviderError):
    pass


class PredictionMarketProvider(ABC):
    """Common, synchronous interface every prediction-market provider must
    implement. All methods are read-only: no order placement, no wallet
    interaction, no authentication that could move funds.
    """

    name: str
    capabilities: ProviderCapabilities

    @abstractmethod
    def fetch_markets(
        self, limit: int = 100, cursor: str | None = None, page_size: int = 100
    ) -> Page[Market]:
        """Fetch active, non-resolved markets. Must paginate transparently up
        to `limit` and never raise on individual malformed records."""

    def fetch_market(self, provider_market_id: str) -> Market | None:
        """Fetch a single market by provider-native ID. Default
        implementation is a linear scan fallback; providers with a direct
        lookup endpoint should override this."""
        page = self.fetch_markets(limit=200)
        for market in page.items:
            if market.provider_market_id == provider_market_id:
                return market
        return None

    def fetch_orderbook(self, provider_market_id: str) -> OrderBook | None:
        """Fetch a live order book snapshot, if the provider exposes one."""
        if not self.capabilities.orderbook:
            raise NotImplementedError(f"{self.name} does not expose an order book")
        raise NotImplementedError

    @abstractmethod
    def fetch_resolved_markets(
        self, limit: int = 100, cursor: str | None = None
    ) -> Page[Market]:
        """Fetch markets that have already resolved, most-recent first."""

    @abstractmethod
    def normalize_market(self, raw: dict[str, Any]) -> Market:
        """Convert a provider-native record into the shared `Market` model."""

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
