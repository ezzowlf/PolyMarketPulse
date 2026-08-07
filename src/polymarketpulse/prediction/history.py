"""Historical submodel — the V1 base-rate blend, now packaged as one
independent ensemble member instead of the whole engine. Looks at
previously *resolved* markets in the same category and provider, takes
their observed YES rate, and reports that as an estimate with a weight that
grows with sample size (capped so a handful of cases can never dominate the
ensemble on their own).
"""

from __future__ import annotations

import sqlite3

from .types import SubmodelEstimate

MIN_COMPARABLE_SAMPLE = 3  # kept for backward-compat imports; graduated tiers below replace the old binary gate
MAX_HISTORY_WEIGHT = 0.6

# Graduated confidence tiers (spec: no pseudo-precise probability from a
# handful of cases). Each tier caps the maximum ensemble weight the
# historical baseline is allowed to carry — a bigger, more trustworthy
# sample earns more say, never a fixed weight regardless of sample size.
TIER_UNAVAILABLE = "unavailable"  # 0-2 cases
TIER_VERY_LOW = "very_low"  # 3-9 cases
TIER_LIMITED = "limited"  # 10-29 cases
TIER_USABLE = "usable"  # 30+ cases

_TIER_MAX_WEIGHT = {TIER_VERY_LOW: 0.15, TIER_LIMITED: 0.35, TIER_USABLE: MAX_HISTORY_WEIGHT}
_TIER_LABEL_DE = {
    TIER_VERY_LOW: "sehr geringe Konfidenz", TIER_LIMITED: "eingeschränkte Konfidenz", TIER_USABLE: "belastbar",
}


def _confidence_tier(sample_size: int) -> str:
    if sample_size < 3:
        return TIER_UNAVAILABLE
    if sample_size < 10:
        return TIER_VERY_LOW
    if sample_size < 30:
        return TIER_LIMITED
    return TIER_USABLE


def compute_history_estimate(
    conn: sqlite3.Connection, category: str | None, provider: str
) -> tuple[SubmodelEstimate, int, float | None]:
    """Returns (estimate, comparable_sample_size, observed_yes_rate)."""
    rows = conn.execute(
        """
        SELECT mr.winning_outcome
        FROM market_resolutions mr
        JOIN markets m ON m.provider = mr.provider AND m.provider_market_id = mr.provider_market_id
        WHERE mr.status = 'resolved' AND m.category = ? AND m.provider = ?
        """,
        (category, provider),
    ).fetchall()
    sample_size = len(rows)

    if sample_size == 0:
        return (
            SubmodelEstimate(
                name="history", estimated_yes_probability=None, weight=0.0, available=False,
                detail=f"Keine historisch aufgelösten Vergleichsmärkte in Kategorie '{category}' gefunden.",
            ),
            0, None,
        )

    yes_count = sum(1 for r in rows if r[0] and r[0].lower() == "yes")
    observed_yes_rate = round(yes_count / sample_size, 4)
    tier = _confidence_tier(sample_size)

    if tier == TIER_UNAVAILABLE:
        return (
            SubmodelEstimate(
                name="history", estimated_yes_probability=observed_yes_rate,
                weight=0.0, available=False,
                detail=(
                    f"{sample_size} vergleichbare(r) Fall/Fälle gefunden (< 3) — "
                    "zu wenig Stichprobe für ein eigenständiges Ensemble-Gewicht (Stufe: unavailable)."
                ),
            ),
            sample_size, observed_yes_rate,
        )

    # Weight scales with sample size within the tier's cap — so a 9-case
    # very_low sample still carries less weight than a 29-case limited one,
    # rather than every sample within a tier getting the exact same weight.
    tier_cap = _TIER_MAX_WEIGHT[tier]
    weight = min(tier_cap, sample_size / 50)
    return (
        SubmodelEstimate(
            name="history", estimated_yes_probability=observed_yes_rate, weight=weight, available=True,
            detail=(
                f"{sample_size} historisch aufgelöste(r) Markt/Märkte in Kategorie '{category}' gefunden, "
                f"davon {yes_count} mit Ausgang YES ({observed_yes_rate:.0%}). "
                f"Konfidenzstufe: {_TIER_LABEL_DE[tier]} ({sample_size} Fälle)."
            ),
        ),
        sample_size, observed_yes_rate,
    )
