from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ResolutionStatus(str, Enum):
    UNRESOLVED = "unresolved"
    PROPOSED = "proposed"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    INVALID = "invalid"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Market:
    """Normalized, provider-agnostic prediction-market representation.

    Provider-specific fields that don't map cleanly onto this shape belong in
    `provider_data` (kept as a plain dict, serialized to JSON in storage) so
    the core model stays stable across providers.
    """

    provider: str
    provider_market_id: str
    condition_id: str
    question: str
    slug: str
    description: str | None = None
    event_id: str | None = None
    category: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    outcomes: tuple[str, ...] = field(default_factory=tuple)
    outcome_prices: tuple[float | None, ...] = field(default_factory=tuple)
    yes_price: float | None = None
    no_price: float | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    best_bid: float | None = None
    best_ask: float | None = None

    liquidity: float = 0.0
    volume_24h: float = 0.0
    volume_total: float = 0.0
    spread: float | None = None
    one_day_change: float | None = None

    created_at: datetime | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    updated_at: datetime | None = None

    resolved_at: datetime | None = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    winning_outcome: str | None = None
    resolution_source: str | None = None

    url: str = ""
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    provider_data: dict = field(default_factory=dict)
    raw_data_hash: str | None = None

    # Legacy alias used by older code paths / tests.
    @property
    def market_id(self) -> str:
        return self.provider_market_id


@dataclass(frozen=True)
class Signal:
    market: Market
    signal_type: str
    score: float
    reasons: tuple[str, ...]
    subfactors: dict = field(default_factory=dict)
    forecast_probability: float | None = None
