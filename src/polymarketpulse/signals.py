from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

from .models import Market, Signal
from .scoring import opportunity_score

# Signal type vocabulary. Deliberately excludes any language implying a sure
# thing, a buy instruction, or a guaranteed edge — these are observations
# about market mechanics, not trading advice.
LIQUIDITY_SURGE = "LIQUIDITY_SURGE"
VOLUME_SURGE = "VOLUME_SURGE"
SPREAD_COMPRESSION = "SPREAD_COMPRESSION"
SPREAD_EXPANSION = "SPREAD_EXPANSION"
PRICE_MOMENTUM = "PRICE_MOMENTUM"
PRICE_REVERSAL = "PRICE_REVERSAL"
NEW_MARKET = "NEW_MARKET"
RESOLUTION_APPROACHING = "RESOLUTION_APPROACHING"
DATA_QUALITY_WARNING = "DATA_QUALITY_WARNING"
CROSS_PROVIDER_DIVERGENCE = "CROSS_PROVIDER_DIVERGENCE"

ALL_SIGNAL_TYPES = (
    LIQUIDITY_SURGE,
    VOLUME_SURGE,
    SPREAD_COMPRESSION,
    SPREAD_EXPANSION,
    PRICE_MOMENTUM,
    PRICE_REVERSAL,
    NEW_MARKET,
    RESOLUTION_APPROACHING,
    DATA_QUALITY_WARNING,
    CROSS_PROVIDER_DIVERGENCE,
)


class PreviousSnapshot(NamedTuple):
    liquidity: float | None
    volume_24h: float | None
    spread: float | None
    yes_price: float | None
    one_day_change: float | None


def generate_signals(
    market: Market,
    previous: PreviousSnapshot | None = None,
    now: datetime | None = None,
) -> list[Signal]:
    """Derive zero or more discrete, explainable signals for one market.

    Every signal carries the same base opportunity score/reasons/subfactors
    as context, plus a specific `signal_type` and the delta that triggered
    it. `forecast_probability` is left unset here since this scanner does
    not produce independent probability forecasts — only downstream tooling
    with an actual model should populate it.
    """
    now = now or datetime.now(UTC)
    result = opportunity_score(market, now=now)
    signals: list[Signal] = []

    def make(signal_type: str, extra_reason: str, extra_subfactors: dict) -> Signal:
        return Signal(
            market=market,
            signal_type=signal_type,
            score=result.score,
            reasons=(*result.reasons, extra_reason),
            subfactors={**result.subfactors, **extra_subfactors},
            forecast_probability=None,
        )

    if market.missing_fields:
        signals.append(
            make(
                DATA_QUALITY_WARNING,
                f"{len(market.missing_fields)} Felder fehlen: {', '.join(market.missing_fields)}",
                {"missing_field_count": len(market.missing_fields)},
            )
        )

    if market.start_at is not None:
        age_days = (now - market.start_at).total_seconds() / 86400
        if age_days < 2:
            signals.append(
                make(NEW_MARKET, f"Markt ist {age_days:.1f} Tage alt", {"age_days": age_days})
            )

    if market.end_at is not None:
        days_left = (market.end_at - now).total_seconds() / 86400
        if 0 <= days_left <= 3:
            signals.append(
                make(
                    RESOLUTION_APPROACHING,
                    f"Auflösung in {days_left:.1f} Tagen",
                    {"days_left": days_left},
                )
            )

    if previous is not None:
        if previous.liquidity and market.liquidity and previous.liquidity > 0:
            change = (market.liquidity - previous.liquidity) / previous.liquidity
            if change >= 0.25:
                signals.append(
                    make(
                        LIQUIDITY_SURGE,
                        f"Liquidität +{change:.0%} seit letztem Snapshot",
                        {"liquidity_change_pct": change},
                    )
                )

        if previous.volume_24h and market.volume_24h and previous.volume_24h > 0:
            change = (market.volume_24h - previous.volume_24h) / previous.volume_24h
            if change >= 0.5:
                signals.append(
                    make(
                        VOLUME_SURGE,
                        f"24h-Volumen +{change:.0%} seit letztem Snapshot",
                        {"volume_change_pct": change},
                    )
                )

        if previous.spread is not None and market.spread is not None:
            delta = market.spread - previous.spread
            if delta <= -0.02:
                signals.append(
                    make(
                        SPREAD_COMPRESSION,
                        f"Spread von {previous.spread:.1%} auf {market.spread:.1%} gefallen",
                        {"spread_delta": delta},
                    )
                )
            elif delta >= 0.03:
                signals.append(
                    make(
                        SPREAD_EXPANSION,
                        f"Spread von {previous.spread:.1%} auf {market.spread:.1%} gestiegen",
                        {"spread_delta": delta},
                    )
                )

        if previous.yes_price is not None and market.yes_price is not None:
            price_delta = market.yes_price - previous.yes_price
            if abs(price_delta) >= 0.07:
                signals.append(
                    make(
                        PRICE_MOMENTUM,
                        f"YES-Preis von {previous.yes_price:.1%} auf {market.yes_price:.1%}",
                        {"price_delta": price_delta},
                    )
                )
            if (
                previous.one_day_change is not None
                and market.one_day_change is not None
                and previous.one_day_change != 0
                and (previous.one_day_change > 0) != (market.one_day_change > 0)
                and abs(market.one_day_change) >= 0.02
            ):
                signals.append(
                    make(
                        PRICE_REVERSAL,
                        "24h-Preisrichtung hat sich seit letztem Snapshot umgekehrt",
                        {
                            "previous_one_day_change": previous.one_day_change,
                            "current_one_day_change": market.one_day_change,
                        },
                    )
                )

    return signals
