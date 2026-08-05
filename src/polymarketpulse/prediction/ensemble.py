"""Ensemble meta-model — combines the independent submodels' estimates
into one final probability via a simple weighted average (weights already
carry each submodel's own confidence/sample-size/deadline adjustments — see
history.py, momentum.py, news.py, deadline.py). A weighted average is the
transparent, auditable choice here: every submodel's contribution to the
final number is visible and traceable, unlike a fitted/learned combiner.
"""

from __future__ import annotations

from .types import SubmodelEstimate


def combine_submodels(estimates: list[SubmodelEstimate]) -> tuple[float | None, list[SubmodelEstimate]]:
    """Returns (final_estimated_yes_probability, all_estimates_including_unavailable).

    Unavailable submodels are kept in the returned list (for full
    transparency in the dashboard/API) but excluded from the weighted
    average itself.
    """
    available = [e for e in estimates if e.available and e.estimated_yes_probability is not None and e.weight > 0]
    if not available:
        return None, estimates

    total_weight = sum(e.weight for e in available)
    if total_weight <= 0:
        return None, estimates

    blended = sum(e.estimated_yes_probability * e.weight for e in available) / total_weight  # type: ignore[misc]
    return round(max(0.0, min(1.0, blended)), 4), estimates
