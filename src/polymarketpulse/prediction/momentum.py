"""Momentum / Market submodel — turns the same transparent snapshot
statistics already computed by `price_analytics.py` into a probability
*adjustment* relative to the current market price. Deliberately simple and
fully documented: no ML, no fitted parameters.

The idea: sustained one-directional price movement with rising liquidity is
weak evidence the market hasn't fully priced in new information yet
(momentum continuation). A market that has just made an unusually large,
isolated move with falling liquidity is weak evidence of overreaction
(mean reversion). Both signals are intentionally capped small — this
submodel nudges the ensemble, it does not dominate it.
"""

from __future__ import annotations

from ..price_analytics import PriceAnalytics, PricePoint, compute_price_analytics

MAX_MOMENTUM_ADJUSTMENT = 0.05  # +/- 5 percentage points, hard cap
UNUSUAL_MOVE_STDEV_MULTIPLE = 2.0


def compute_momentum_estimate(
    points: list[PricePoint], market_yes_price: float | None
) -> tuple[float | None, PriceAnalytics | None, str]:
    """Returns (estimated_yes_probability, analytics, detail).

    The current market price itself is always the anchor when available —
    prediction markets are usually reasonably well-calibrated, so losing
    that signal entirely just because snapshot history is thin would be
    worse than including it unadjusted. Momentum/mean-reversion only ever
    *adjust* that anchor, and only once enough history exists (>= 3
    snapshots) to compute them meaningfully; with less history the
    submodel still reports the bare market price with a note explaining
    why no adjustment was applied."""
    if market_yes_price is None:
        return None, None, "Kein aktueller Marktpreis vorhanden — kein Momentum-Signal möglich."
    if len(points) < 3:
        return (
            round(market_yes_price, 4), None,
            "Zu wenige Preis-Snapshots für Momentum-/Reversion-Anpassung (< 3) — Marktpreis unangepasst übernommen.",
        )

    analytics = compute_price_analytics(points)
    if analytics.price_change is None or analytics.volatility is None:
        return round(market_yes_price, 4), analytics, "Preisverlauf unvollständig — Marktpreis unangepasst übernommen."

    adjustment = 0.0
    notes: list[str] = []

    # 1) Momentum continuation: reward the direction of the recent trend,
    #    scaled by the short vs. long moving-average gap (a simple, standard
    #    trend-strength proxy) and damped by volatility (noisy markets get
    #    less momentum credit).
    if analytics.moving_average_short is not None and analytics.moving_average_long is not None:
        ma_gap = analytics.moving_average_short - analytics.moving_average_long
        damping = 1.0 / (1.0 + (analytics.volatility * 20))
        momentum_component = max(-0.03, min(0.03, ma_gap * 0.6 * damping))
        adjustment += momentum_component
        if abs(momentum_component) > 0.005:
            notes.append(
                f"Momentum: kurzer/langer gleitender Durchschnitt {analytics.moving_average_short:.3f}/"
                f"{analytics.moving_average_long:.3f} -> Trendkomponente {momentum_component:+.1%}."
            )

    # 2) Mean reversion: an isolated single-step move much larger than the
    #    recent volatility pattern gets a small counter-weight, since such
    #    moves partially revert more often than they persist in
    #    high-liquidity, well-arbitraged markets.
    if analytics.max_price_change is not None and analytics.volatility > 0:
        move_in_stdevs = analytics.max_price_change / analytics.volatility
        if move_in_stdevs >= UNUSUAL_MOVE_STDEV_MULTIPLE and analytics.trend_reversals > 0:
            reversion_component = -0.15 * (analytics.price_change or 0.0)
            reversion_component = max(-0.02, min(0.02, reversion_component))
            adjustment += reversion_component
            notes.append(
                f"Mean-Reversion: ungewöhnliche Einzelbewegung ({move_in_stdevs:.1f}x Volatilität) mit "
                f"{analytics.trend_reversals} Trendwechseln -> Gegenkomponente {reversion_component:+.1%}."
            )

    adjustment = max(-MAX_MOMENTUM_ADJUSTMENT, min(MAX_MOMENTUM_ADJUSTMENT, adjustment))
    estimate = max(0.0, min(1.0, market_yes_price + adjustment))

    if not notes:
        notes.append("Preisverlauf stabil, kein signifikantes Momentum- oder Reversion-Signal.")

    return round(estimate, 4), analytics, " ".join(notes)
