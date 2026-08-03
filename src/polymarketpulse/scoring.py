from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .models import Market


@dataclass(frozen=True)
class ScoreResult:
    score: float
    reasons: tuple[str, ...]
    subfactors: dict = field(default_factory=dict)


def opportunity_score(market: Market, now: datetime | None = None) -> ScoreResult:
    """Transparent, explainable *research* score built only from observable
    market mechanics (liquidity, volume, spread, movement, timing, data
    quality). Not a fair-value estimate, not a win probability, not a buy
    signal. A real edge requires an independently estimated fair probability
    plus historical calibration, done outside this scanner.
    """
    now = now or datetime.now(UTC)
    score = 0.0
    reasons: list[str] = []
    subfactors: dict[str, float] = {}

    if market.liquidity > 0:
        points = min(20.0, max(0.0, math.log10(market.liquidity) - 2.0) * 8.0)
        score += points
        subfactors["liquidity"] = points
        if market.liquidity >= 25_000:
            reasons.append("gute Liquidität")

    if market.volume_24h > 0:
        points = min(20.0, max(0.0, math.log10(market.volume_24h) - 2.0) * 8.0)
        score += points
        subfactors["volume_24h"] = points
        if market.volume_24h >= 10_000:
            reasons.append("hohes 24h-Volumen")

    if market.spread is not None:
        if market.spread <= 0.02:
            points = 15.0
            reasons.append("enger Spread")
        elif market.spread <= 0.05:
            points = 7.0
        elif market.spread >= 0.10:
            points = -10.0
            reasons.append("weiter Spread")
        else:
            points = 0.0
        score += points
        subfactors["spread"] = points

    if market.one_day_change is not None:
        move = abs(market.one_day_change)
        points = min(15.0, move * 150.0)
        score += points
        subfactors["price_movement"] = points
        if move >= 0.05:
            reasons.append(f"starke 24h-Bewegung ({move:.1%})")

    if market.yes_price is not None:
        if 0.15 <= market.yes_price <= 0.85:
            points = 10.0
            reasons.append("nicht bereits nahezu entschieden")
        elif market.yes_price <= 0.03 or market.yes_price >= 0.97:
            points = -5.0
        else:
            points = 0.0
        score += points
        subfactors["decisiveness"] = points

    if market.end_at is not None:
        days_left = (market.end_at - now).total_seconds() / 86400
        if days_left < 0:
            reasons.append("Enddatum liegt in der Vergangenheit (Daten prüfen)")
        elif 3 <= days_left <= 90:
            score += 10.0
            subfactors["time_to_resolution"] = 10.0
            reasons.append(f"Auflösung in {days_left:.0f} Tagen")
        elif days_left < 3:
            score += 3.0
            subfactors["time_to_resolution"] = 3.0
            reasons.append("Auflösung sehr bald")

    if market.start_at is not None:
        age_days = (now - market.start_at).total_seconds() / 86400
        if age_days >= 14:
            score += 5.0
            subfactors["market_age"] = 5.0
        elif age_days < 2:
            reasons.append("sehr junger Markt (wenig Handelshistorie)")

    if market.volume_total > 0 and market.volume_24h > 0:
        share = market.volume_24h / market.volume_total
        if share >= 0.15:
            points = 10.0
            reasons.append(f"ungewöhnliche Aktivität ({share:.0%} des Gesamtvolumens in 24h)")
        elif share >= 0.05:
            points = 4.0
        else:
            points = 0.0
        score += points
        subfactors["unusual_activity"] = points

    if market.missing_fields:
        penalty = min(15.0, len(market.missing_fields) * 3.0)
        score -= penalty
        subfactors["data_quality_penalty"] = -penalty
        reasons.append(f"unvollständige Daten ({len(market.missing_fields)} Felder fehlen)")

    return ScoreResult(
        score=round(max(0.0, min(100.0, score)), 1),
        reasons=tuple(reasons),
        subfactors=subfactors,
    )
