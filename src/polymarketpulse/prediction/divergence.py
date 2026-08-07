"""Divergence safety — Phase B4 of the "no fabricated confidence" fix.

A big gap between the engine's independent (market-blind) estimate and the
market's own price is exactly the kind of signal this product exists to
surface — real, evidence-backed edges are the whole point. But an
*unjustified* gap (little/no real evidence, no base rate, low source
diversity) backed by nothing more than a weak Bayesian nudge off a neutral
prior is not a genuine edge, it's noise dressed up as a probability. This
module implements the shared safety check: when the gap exceeds
`DIVERGENCE_THRESHOLD_PP` and the evidence behind the independent estimate is
judged weak, the forecast must be suppressed rather than reported.

`DIVERGENCE_THRESHOLD_PP` is defined once here so later phases (per the
spec, "Phase M") that need the same 15-percentage-point threshold reuse this
exact constant instead of re-declaring it with potential drift.
"""

from __future__ import annotations

from dataclasses import dataclass

# 15 percentage points — shared constant, see module docstring.
DIVERGENCE_THRESHOLD_PP = 0.15


@dataclass(frozen=True)
class DivergenceSafetyResult:
    suppressed: bool
    gap: float | None
    reason: str | None


def evaluate_divergence_safety(
    independent_probability: float | None,
    market_probability: float | None,
    evidence_is_strong: bool,
) -> DivergenceSafetyResult:
    """Decides whether a divergence between the independent estimate and the
    market price is backed by strong-enough evidence to stand, or must be
    suppressed.

    `evidence_is_strong` is a plain bool the caller (engine.py) computes from
    fields it already has (comparable historical sample size, count/tier of
    linked evidence, source diversity, base-rate availability) — this
    function only owns the threshold/suppression decision, not what counts
    as "strong", since that judgment needs context (history vs. independent
    evidence vs. base rate) this module doesn't have.
    """
    if independent_probability is None or market_probability is None:
        return DivergenceSafetyResult(suppressed=False, gap=None, reason=None)

    gap = round(abs(independent_probability - market_probability), 4)
    if gap < DIVERGENCE_THRESHOLD_PP:
        return DivergenceSafetyResult(suppressed=False, gap=gap, reason=None)

    if evidence_is_strong:
        # Big gap, but backed by real evidence (a real historical sample, or
        # multiple direct/primary-source-tier evidence items) — this is a
        # genuine edge, not suppressed.
        return DivergenceSafetyResult(suppressed=False, gap=gap, reason=None)

    reason = (
        f"Forecast suppressed: independent estimate diverges from the market price by "
        f"{gap:.1%}, exceeding the {DIVERGENCE_THRESHOLD_PP:.0%} safety threshold, but the "
        "evidence behind the independent estimate is too weak (insufficient historical "
        "sample, insufficient direct/primary-source evidence, and/or no defensible base "
        "rate) to justify reporting a number that far from the market consensus."
    )
    return DivergenceSafetyResult(suppressed=True, gap=gap, reason=reason)
