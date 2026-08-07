"""Ensemble meta-model — combines the independent submodels' estimates
into one final probability via a simple weighted average (weights already
carry each submodel's own confidence/sample-size/deadline adjustments — see
history.py, momentum.py, news.py, deadline.py). A weighted average is the
transparent, auditable choice here: every submodel's contribution to the
final number is visible and traceable, unlike a fitted/learned combiner.

Weighting formula (Phase F): `combine_submodels` itself is unchanged — a
plain weight-normalized average, `sum(p_i * w_i) / sum(w_i)`. What changed
is *where the w_i come from*. Every submodel is required to derive its own
`weight` from a real quality signal specific to that submodel, never a flat
constant gated only on "did it return a number":

  - history.py:            weight scales with the Phase D effective sample
                            size (Kish's ESS) of its similarity-weighted
                            comparable-case baseline, capped per confidence
                            tier (very_low/limited/usable). See history.py.
  - independent_evidence:  weight = quality_scaled_weight(base, quality)
                            where quality combines evidence.py's own
                            source_quality_score (reliability * recency *
                            topical-link-confidence * entailment-strength,
                            averaged over scored evidence) with a
                            confirmation-count factor — see engine.py.
  - quant / macro / politics / geopolitics / sports (Phase E specialized
                            models): weight = quality_scaled_weight(base,
                            confidence/100) using each model's own
                            `confidence` field (0..100), which is itself
                            derived from real inputs each model already
                            computes (z-score magnitude and data
                            completeness for quant; source/event-strength
                            heuristics for the others — see engine.py's
                            wiring and each model's own module for exactly
                            how its confidence is derived).
  - momentum / news / event_relations: unchanged — price-anchored
                            submodels outside the independent bucket, see
                            engine.py `_forecast_status`.

No submodel is ever given nonzero weight purely because `available=True`;
`available=True` with a low/zero quality signal still yields a small (or
zero, if `quality_scaled_weight` rounds to 0.0) share of the blend.
"""

from __future__ import annotations

from .types import SubmodelEstimate


def quality_scaled_weight(base_weight: float, quality_0_1: float) -> float:
    """The one place a submodel's genuine quality signal (already 0..1,
    e.g. `confidence / 100` or a source-quality/confirmation-count blend)
    is turned into an ensemble weight: `base_weight * quality`, clamped so
    a garbage-in quality value (negative, >1, NaN-adjacent) can never
    produce a weight outside [0, base_weight]. `base_weight` is the ceiling
    the submodel could reach at quality=1.0 — how much say it gets even
    when maximally confident, set per-submodel at the call site (see
    engine.py) so structurally weaker submodels (e.g. specialized models
    with no historical calibration yet) never outweigh better-evidenced
    ones just by both claiming confidence=100."""
    quality = max(0.0, min(1.0, quality_0_1))
    return round(max(0.0, base_weight) * quality, 4)


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
