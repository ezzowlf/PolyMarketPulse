"""Confidence score — separate from the probability estimate itself. A
model can be very sure the coin is fair (high confidence, p=0.5) or very
unsure about a lopsided-looking market (low confidence, p=0.8). Conflating
the two is exactly the mistake the whole Phase-7/V2 architecture exists to
prevent (see ai/prompts.py rule 7: "a score of 80 is never automatically an
80% probability").
"""

from __future__ import annotations

from datetime import UTC, datetime

from .types import DataQualityBreakdown, SubmodelEstimate

# --- J1: real freshness (Aktualität) computation --------------------------
# Prior to this fix, engine.py hardcoded `aktualitaet=85.0` unconditionally
# (see the removed KNOWN LIMITATION comment there) — a fixed value regardless
# of how stale the underlying evidence/price data actually was. This
# replaces it with a real, source-type-aware decay computed from actual
# timestamps: independently-sourced news/evidence uses each item's own
# recency_weight (already computed honestly in evidence.py from
# published_at vs now, 24h half-life there), and price/quant data uses the
# most recent market_snapshots.captured_at with a *much* shorter half-life
# (6h) because a stale price snapshot goes stale far faster than a news
# article's topical relevance does. When neither timestamped signal is
# available at all, this reports a neutral 50.0 (never a flattering
# fixed number) — an honest "we don't actually know how fresh this is",
# not a fabricated high score.
_PRICE_FRESHNESS_HALF_LIFE_HOURS = 6.0
_NO_TIMESTAMP_FALLBACK = 50.0


def compute_freshness_score(
    evidence_recency_weights: list[float],
    latest_price_captured_at: str | None,
    now: datetime | None = None,
    price_signal_is_primary: bool = False,
) -> tuple[float, str]:
    """Returns (aktualitaet 0..100, detail). `evidence_recency_weights` is the
    list of individual EvidenceFactor.recency_weight values (0..1, already
    decayed from real published_at timestamps) for whatever evidence fed
    this market's independent_evidence submodel. `latest_price_captured_at`
    is the most recent market_snapshots.captured_at ISO timestamp, or None
    if no price history exists. `price_signal_is_primary` should be True for
    quant/price-threshold markets, where price freshness matters more than
    news freshness."""
    now = now or datetime.now(UTC)
    signals: list[tuple[float, float]] = []  # (score_0_100, weight)

    if evidence_recency_weights:
        avg_recency = sum(evidence_recency_weights) / len(evidence_recency_weights)
        signals.append((avg_recency * 100, 0.4 if price_signal_is_primary else 0.7))

    if latest_price_captured_at:
        try:
            captured = datetime.fromisoformat(latest_price_captured_at)
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=UTC)
            hours_ago = max(0.0, (now - captured).total_seconds() / 3600)
            price_recency = 0.5 ** (hours_ago / _PRICE_FRESHNESS_HALF_LIFE_HOURS)
            signals.append((price_recency * 100, 0.6 if price_signal_is_primary else 0.3))
        except (ValueError, TypeError):
            pass

    if not signals:
        return (
            _NO_TIMESTAMP_FALLBACK,
            (
                "Aktualität: keine echten Zeitstempel (weder Evidenz noch Preis-Snapshot) verfügbar — "
                "neutraler Fallback, kein hartkodierter Wert."
            ),
        )

    total_weight = sum(w for _, w in signals)
    score = sum(v * w for v, w in signals) / total_weight
    return (
        round(score, 1),
        f"Aktualität aus {len(signals)} echten Zeitstempel-Signal(en) berechnet (Score={score:.1f}).",
    )


def compute_confidence(
    data_quality: DataQualityBreakdown,
    submodel_estimates: list[SubmodelEstimate],
    market_stability: float,  # 0..1, e.g. 1 - normalized volatility
    deadline_phase_known: bool,
) -> tuple[float, float | None]:
    """Returns (confidence_score 0..100, ensemble_agreement 0..1 or None).

    Components:
    - data quality (as before, 0-100, weighted 35%)
    - number of *available* submodels contributing (more independent
      signals agreeing = more trustworthy), weighted 25%
    - ensemble agreement: how close the available submodels' estimates are
      to each other (low spread = high agreement), weighted 25%
    - market stability (calmer recent price action = more trustworthy
      snapshot), weighted 15%
    """
    available = [s for s in submodel_estimates if s.available and s.estimated_yes_probability is not None]
    n_available = len(available)
    coverage_score = min(100.0, n_available * 25.0)  # 4 submodels -> 100

    agreement: float | None = None
    agreement_score = 50.0  # neutral default when we can't measure agreement
    if n_available >= 2:
        values = [s.estimated_yes_probability for s in available]  # type: ignore[misc]
        spread = max(values) - min(values)
        agreement = round(max(0.0, 1 - spread / 0.5), 4)  # spread >= 0.5 -> 0 agreement
        agreement_score = agreement * 100

    stability_score = max(0.0, min(1.0, market_stability)) * 100

    confidence = round(
        data_quality.total * 0.35 + coverage_score * 0.25 + agreement_score * 0.25 + stability_score * 0.15,
        1,
    )
    if not deadline_phase_known:
        confidence = round(confidence * 0.9, 1)  # small penalty for unknown resolution timing

    return min(100.0, confidence), agreement
