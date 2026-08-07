"""Phase N2 — calibration framework (report-only infrastructure).

Standard, hand-verifiable implementations of the metrics needed to answer
"are our forecasts actually calibrated?" once enough resolved history
exists to check: Brier score, log-loss, reliability-diagram bins, and
error broken down by model family.

This module is deliberately pure computation over `list[tuple[float,
bool]]` (predicted probability, actual binary outcome) — it never reads
from storage directly except in `compute_calibration_report`, and even
there the read is a read-only JOIN against already-resolved markets. No
function in this module ever fabricates or estimates an outcome; every
metric is computed only from real matched (prediction, resolved-outcome)
pairs.

Look-ahead guard (the single most important invariant in this file):
`compute_calibration_report` only joins a `prediction_snapshots` row to a
`market_resolutions` row when
    snapshot.forecast_at < resolution.resolved_at
i.e. the forecast must have been made strictly BEFORE the market
resolved. A snapshot whose `forecast_at` is at or after the resolution's
`resolved_at` is excluded from the matched set entirely — it would mean
either the forecast was computed with knowledge of (or after) the
outcome, or a clock/ordering anomaly, and either way it must not be
allowed to inflate or deflate a calibration score.

Minimum sample size: real matched pairs below `MIN_MATCHED_PAIRS_FOR_CALIBRATION`
(documented below) yield `status="UNCALIBRATED"` with metrics omitted —
publishing a Brier score computed from, say, 3 resolved forecasts would be
worse than useless: it would look authoritative while being pure noise,
and a few lucky/unlucky resolutions could swing it wildly. 20 is the
low end of what's conventionally treated as enough data to say anything
about a probability-calibration curve without single-observation swings
dominating the number (e.g. a 10-bin reliability diagram needs several
points per bin to mean anything at all, and 20 pairs already gives some
bins zero points) — this is a deliberately conservative floor, not a
statistically "proven" cutoff, and is expected to move up once real
calibration work begins in earnest.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

# See module docstring for the reasoning behind this specific floor.
MIN_MATCHED_PAIRS_FOR_CALIBRATION = 20

# Clipping bound for log_loss so a single prediction of exactly 0.0 or 1.0
# paired with the "wrong" outcome doesn't blow up to +inf.
_LOG_LOSS_EPS = 1e-15


@dataclass(frozen=True)
class BinResult:
    """One bucket of a reliability diagram."""

    bin_lower: float
    bin_upper: float
    mean_predicted_probability: float | None
    observed_frequency: float | None
    count: int

    def as_dict(self) -> dict:
        return {
            "bin_lower": self.bin_lower,
            "bin_upper": self.bin_upper,
            "mean_predicted_probability": self.mean_predicted_probability,
            "observed_frequency": self.observed_frequency,
            "count": self.count,
        }


@dataclass(frozen=True)
class CalibrationReport:
    status: str  # "UNCALIBRATED" or "CALIBRATED"
    matched_pair_count: int
    min_required: int
    brier_score: float | None = None
    log_loss_value: float | None = None
    bins: tuple[BinResult, ...] = field(default_factory=tuple)
    error_by_family: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "matched_pair_count": self.matched_pair_count,
            "min_required": self.min_required,
            "brier_score": self.brier_score,
            "log_loss_value": self.log_loss_value,
            "bins": [b.as_dict() for b in self.bins],
            "error_by_family": self.error_by_family,
        }


def brier_score(predictions_and_outcomes: list[tuple[float, bool]]) -> float:
    """Mean squared error between predicted probability and binary outcome
    (0/1). 0.0 is perfect, 0.25 is what an always-predict-0.5 model scores
    against a 50/50 base rate, 1.0 is maximally wrong."""
    if not predictions_and_outcomes:
        raise ValueError("brier_score requires at least one (prediction, outcome) pair")
    total = 0.0
    for prob, outcome in predictions_and_outcomes:
        y = 1.0 if outcome else 0.0
        total += (prob - y) ** 2
    return total / len(predictions_and_outcomes)


def log_loss(predictions_and_outcomes: list[tuple[float, bool]]) -> float:
    """Standard binary log loss (cross-entropy), with clipping to avoid
    log(0) when a prediction is exactly 0.0 or 1.0."""
    if not predictions_and_outcomes:
        raise ValueError("log_loss requires at least one (prediction, outcome) pair")
    total = 0.0
    for prob, outcome in predictions_and_outcomes:
        p = min(max(prob, _LOG_LOSS_EPS), 1.0 - _LOG_LOSS_EPS)
        y = 1.0 if outcome else 0.0
        total += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
    return total / len(predictions_and_outcomes)


def calibration_bins(
    predictions_and_outcomes: list[tuple[float, bool]], n_bins: int = 10
) -> list[BinResult]:
    """Reliability-diagram data: for each of `n_bins` equal-width probability
    buckets over [0, 1], the mean predicted probability, the actual outcome
    frequency, and the count of items falling in that bin. Empty bins are
    still returned (count=0, means as None) so callers can render a
    complete diagram without having to special-case gaps."""
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")

    bin_width = 1.0 / n_bins
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for prob, outcome in predictions_and_outcomes:
        clamped = min(max(prob, 0.0), 1.0)
        idx = min(int(clamped / bin_width), n_bins - 1)
        buckets[idx].append((prob, outcome))

    results: list[BinResult] = []
    for i, items in enumerate(buckets):
        lower = i * bin_width
        upper = (i + 1) * bin_width
        if items:
            mean_pred = sum(p for p, _ in items) / len(items)
            observed = sum(1.0 for _, o in items if o) / len(items)
        else:
            mean_pred = None
            observed = None
        results.append(BinResult(lower, upper, mean_pred, observed, len(items)))
    return results


def error_by_model_family(
    snapshots_with_outcomes: list[tuple[str, float, bool]], family_field: str | None = None
) -> dict[str, float]:
    """Brier score grouped by whatever field distinguishes model families.

    `snapshots_with_outcomes` is a list of (family_label, predicted_probability,
    outcome) triples — the caller (e.g. `compute_calibration_report`) is
    responsible for extracting the family label from whichever snapshot
    field it wants to group by (e.g. the primary/first contributing model
    name from `models_used`). `family_field` is accepted for documentation
    / call-site clarity only (it plays no role in the computation itself
    since the label is already resolved by the caller).
    """
    del family_field  # documentation-only parameter, see docstring
    grouped: dict[str, list[tuple[float, bool]]] = {}
    for family, prob, outcome in snapshots_with_outcomes:
        grouped.setdefault(family, []).append((prob, outcome))
    return {family: brier_score(pairs) for family, pairs in grouped.items()}


def compute_calibration_report(storage_connection: sqlite3.Connection) -> CalibrationReport:
    """Joins Phase N's persisted shadow snapshots against real resolved
    outcomes in `market_resolutions`, with a hard look-ahead guard:
    `snapshot.forecast_at < resolution.resolved_at`. Only markets whose
    resolution genuinely happened after the forecast was written are
    counted. Real metrics are only computed once
    `MIN_MATCHED_PAIRS_FOR_CALIBRATION` matched pairs genuinely exist;
    below that, `status="UNCALIBRATED"` is returned with the honest (real,
    possibly zero) matched-pair count and no fabricated metrics.
    """
    rows = storage_connection.execute(
        """
        SELECT s.calibrated_probability, s.blended_probability, s.market_probability_at_forecast,
               r.winning_outcome, s.models_used
        FROM prediction_snapshots s
        JOIN market_resolutions r
          ON r.provider = s.provider AND r.provider_market_id = s.provider_market_id
        WHERE s.forecast_at IS NOT NULL
          AND r.resolved_at IS NOT NULL
          AND s.forecast_at < r.resolved_at
          AND r.winning_outcome IN ('Yes', 'No')
        """
    ).fetchall()

    pairs: list[tuple[float, bool]] = []
    family_triples: list[tuple[str, float, bool]] = []
    for calibrated_probability, blended_probability, market_probability_at_forecast, winning_outcome, models_used in rows:
        # Prefer calibrated_probability, fall back through the chain of
        # increasingly-raw forecast numbers — never fall back to anything
        # resolution-derived.
        prob = calibrated_probability
        if prob is None:
            prob = blended_probability
        if prob is None:
            prob = market_probability_at_forecast
        if prob is None:
            continue
        outcome = winning_outcome == "Yes"
        pairs.append((prob, outcome))
        family = (models_used or "unknown").split(",")[0] or "unknown"
        family_triples.append((family, prob, outcome))

    matched_pair_count = len(pairs)
    if matched_pair_count < MIN_MATCHED_PAIRS_FOR_CALIBRATION:
        return CalibrationReport(
            status="UNCALIBRATED",
            matched_pair_count=matched_pair_count,
            min_required=MIN_MATCHED_PAIRS_FOR_CALIBRATION,
        )

    return CalibrationReport(
        status="CALIBRATED",
        matched_pair_count=matched_pair_count,
        min_required=MIN_MATCHED_PAIRS_FOR_CALIBRATION,
        brier_score=brier_score(pairs),
        log_loss_value=log_loss(pairs),
        bins=tuple(calibration_bins(pairs)),
        error_by_family=error_by_model_family(family_triples),
    )
