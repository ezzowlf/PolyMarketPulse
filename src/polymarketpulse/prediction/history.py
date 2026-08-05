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

MIN_COMPARABLE_SAMPLE = 5
MAX_HISTORY_WEIGHT = 0.6


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

    if sample_size < MIN_COMPARABLE_SAMPLE:
        return (
            SubmodelEstimate(
                name="history", estimated_yes_probability=observed_yes_rate,
                weight=0.0, available=False,
                detail=(
                    f"{sample_size} vergleichbare(r) Fall/Fälle gefunden (< {MIN_COMPARABLE_SAMPLE} nötig) — "
                    "zu wenig Stichprobe für ein eigenständiges Ensemble-Gewicht."
                ),
            ),
            sample_size, observed_yes_rate,
        )

    weight = min(MAX_HISTORY_WEIGHT, sample_size / 50)
    return (
        SubmodelEstimate(
            name="history", estimated_yes_probability=observed_yes_rate, weight=weight, available=True,
            detail=(
                f"{sample_size} historisch aufgelöste(r) Markt/Märkte in Kategorie '{category}' gefunden, "
                f"davon {yes_count} mit Ausgang YES ({observed_yes_rate:.0%})."
            ),
        ),
        sample_size, observed_yes_rate,
    )
