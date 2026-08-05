"""Bayesian update — folds fresh news evidence into a prior probability via
a log-odds update instead of recomputing everything from scratch. This is
the module that lets "new information raise or lower the probability"
incrementally, as the spec requires, rather than discarding prior state on
every re-run.

log-odds(posterior) = log-odds(prior) + evidence_strength

`evidence_strength` is derived from the news submodel's weighted sentiment
and the deadline phase's `news_weight` multiplier (closer to resolution,
the same news moves the needle further) — both already computed elsewhere,
kept as plain inputs here so this module stays a pure, independently
testable function.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MAX_LOG_ODDS_SHIFT = 1.5  # caps a single update's swing (~ +/-18pp at p=0.5)


def _to_log_odds(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _from_log_odds(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@dataclass(frozen=True)
class BayesianUpdateResult:
    prior_probability: float
    posterior_probability: float
    evidence_strength: float  # log-odds units actually applied (post-cap)
    detail: str


def bayesian_update(
    prior_probability: float,
    weighted_news_sentiment: float | None,
    confirmation_count: int,
    news_weight_multiplier: float,
) -> BayesianUpdateResult:
    """`weighted_news_sentiment` in [-1, 1] or None (no news evidence ->
    posterior == prior, a no-op update, not a silent push toward 0.5)."""
    if weighted_news_sentiment is None or confirmation_count == 0:
        return BayesianUpdateResult(
            prior_probability=round(prior_probability, 4),
            posterior_probability=round(prior_probability, 4),
            evidence_strength=0.0,
            detail="Keine Nachrichtenevidenz vorhanden — Bayesianisches Update ist ein No-Op (Prior bleibt Posterior).",
        )

    # Each independent confirming source adds a fixed increment of evidence
    # strength, scaled by sentiment direction/magnitude and the deadline
    # phase's news weight — more confirmations and stronger sentiment shift
    # the log-odds further, capped to avoid a handful of headlines
    # overwhelming the prior.
    raw_strength = weighted_news_sentiment * min(confirmation_count, 5) * 0.25 * news_weight_multiplier
    evidence_strength = max(-MAX_LOG_ODDS_SHIFT, min(MAX_LOG_ODDS_SHIFT, raw_strength))

    prior_log_odds = _to_log_odds(prior_probability)
    posterior_log_odds = prior_log_odds + evidence_strength
    posterior = _from_log_odds(posterior_log_odds)

    return BayesianUpdateResult(
        prior_probability=round(prior_probability, 4),
        posterior_probability=round(posterior, 4),
        evidence_strength=round(evidence_strength, 4),
        detail=(
            f"Bayesianisches Update: Prior {prior_probability:.1%} + Evidenzstärke {evidence_strength:+.3f} "
            f"(Log-Odds, aus {confirmation_count} bestätigenden Quelle(n) x Stimmung "
            f"{weighted_news_sentiment:+.2f} x Deadline-Gewicht {news_weight_multiplier:.2f}) "
            f"-> Posterior {posterior:.1%}."
        ),
    )
