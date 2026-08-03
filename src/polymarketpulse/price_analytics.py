from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PricePoint:
    captured_at: str
    yes_price: float | None
    liquidity: float | None = None
    volume_24h: float | None = None
    spread: float | None = None
    opportunity_score: float | None = None


@dataclass(frozen=True)
class PriceAnalytics:
    """Every field here is a plain, documented statistical computation over
    the stored snapshot history — no model, no black box."""

    sample_count: int
    price_change: float | None  # last - first
    price_change_pct: float | None  # (last - first) / first
    moving_average_short: float | None  # last N points, N=5
    moving_average_long: float | None  # last N points, N=20
    volatility: float | None  # stdev of period-over-period returns
    average_volume: float | None
    max_price_change: float | None  # largest single-step absolute move
    trend_reversals: int  # sign changes in period-over-period direction
    average_seconds_between_updates: float | None
    liquidity_trend: str  # "steigend" | "fallend" | "stabil" | "unbekannt"
    spread_trend: str

    def as_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "price_change": self.price_change,
            "price_change_pct": self.price_change_pct,
            "moving_average_short": self.moving_average_short,
            "moving_average_long": self.moving_average_long,
            "volatility": self.volatility,
            "average_volume": self.average_volume,
            "max_price_change": self.max_price_change,
            "trend_reversals": self.trend_reversals,
            "average_seconds_between_updates": self.average_seconds_between_updates,
            "liquidity_trend": self.liquidity_trend,
            "spread_trend": self.spread_trend,
        }


def _moving_average(values: list[float], window: int) -> float | None:
    if not values:
        return None
    subset = values[-window:]
    return sum(subset) / len(subset)


def _trend(values: list[float]) -> str:
    if len(values) < 2:
        return "unbekannt"
    delta = values[-1] - values[0]
    threshold = abs(values[0]) * 0.02 if values[0] else 0.02
    if delta > threshold:
        return "steigend"
    if delta < -threshold:
        return "fallend"
    return "stabil"


def compute_price_analytics(points: list[PricePoint]) -> PriceAnalytics:
    priced = [p for p in points if p.yes_price is not None]
    prices = [p.yes_price for p in priced]  # type: ignore[misc]

    price_change = prices[-1] - prices[0] if len(prices) >= 2 else None
    price_change_pct = (price_change / prices[0]) if price_change is not None and prices[0] else None

    returns = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    volatility = statistics.pstdev(returns) if len(returns) >= 2 else None
    max_price_change = max((abs(r) for r in returns), default=None)

    trend_reversals = 0
    last_sign = 0
    for r in returns:
        sign = (r > 0) - (r < 0)
        if sign != 0 and last_sign != 0 and sign != last_sign:
            trend_reversals += 1
        if sign != 0:
            last_sign = sign

    volumes = [p.volume_24h for p in points if p.volume_24h is not None]
    average_volume = sum(volumes) / len(volumes) if volumes else None

    timestamps: list[datetime] = []
    for p in points:
        try:
            timestamps.append(datetime.fromisoformat(p.captured_at))
        except ValueError:
            continue
    gaps = [
        (timestamps[i] - timestamps[i - 1]).total_seconds()
        for i in range(1, len(timestamps))
        if timestamps[i] >= timestamps[i - 1]
    ]
    average_seconds_between_updates = sum(gaps) / len(gaps) if gaps else None

    liquidity_values = [p.liquidity for p in points if p.liquidity is not None]
    spread_values = [p.spread for p in points if p.spread is not None]

    return PriceAnalytics(
        sample_count=len(points),
        price_change=price_change,
        price_change_pct=price_change_pct,
        moving_average_short=_moving_average(prices, 5),
        moving_average_long=_moving_average(prices, 20),
        volatility=volatility,
        average_volume=average_volume,
        max_price_change=max_price_change,
        trend_reversals=trend_reversals,
        average_seconds_between_updates=average_seconds_between_updates,
        liquidity_trend=_trend(liquidity_values),
        spread_trend=_trend(spread_values),
    )
