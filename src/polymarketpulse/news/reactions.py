from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class MarketReaction:
    news_market_link_id: int
    price_before: float | None
    price_after: float | None
    price_change: float | None
    window_hours: float
    reacted: bool  # |price_change| above a small, documented threshold


REACTION_THRESHOLD = 0.02  # 2 percentage points of YES price


def compute_reaction(
    link_id: int,
    published_at: datetime,
    price_history: list[tuple[str, float | None]],
    window_hours: float = 24.0,
) -> MarketReaction:
    """Compare the closest price snapshot before `published_at` to the
    closest one within `window_hours` after it. Pure function over rows the
    caller already fetched from `price_history` — no live requests."""
    window_end = published_at + timedelta(hours=window_hours)

    before_price: float | None = None
    after_price: float | None = None
    for captured_at, yes_price in price_history:
        try:
            ts = datetime.fromisoformat(captured_at)
        except ValueError:
            continue
        if ts <= published_at:
            before_price = yes_price if yes_price is not None else before_price
        elif ts <= window_end and after_price is None and yes_price is not None:
            after_price = yes_price

    price_change = (
        after_price - before_price if before_price is not None and after_price is not None else None
    )
    reacted = price_change is not None and abs(price_change) >= REACTION_THRESHOLD

    return MarketReaction(
        news_market_link_id=link_id,
        price_before=before_price,
        price_after=after_price,
        price_change=price_change,
        window_hours=window_hours,
        reacted=reacted,
    )
