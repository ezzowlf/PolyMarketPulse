"""Confidence score — separate from the probability estimate itself. A
model can be very sure the coin is fair (high confidence, p=0.5) or very
unsure about a lopsided-looking market (low confidence, p=0.8). Conflating
the two is exactly the mistake the whole Phase-7/V2 architecture exists to
prevent (see ai/prompts.py rule 7: "a score of 80 is never automatically an
80% probability").
"""

from __future__ import annotations

from .types import DataQualityBreakdown, SubmodelEstimate


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
