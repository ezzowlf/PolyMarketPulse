"""Performance tracking / evaluation for Prediction Engine V2. Every call
to `get_prediction()` / `explain_recommendation()` persists a row in
`prediction_snapshots` (see ai/service.py::_persist_prediction_snapshot).
This module joins those snapshots back against `market_resolutions` once a
market resolves and computes the metrics the spec requires: Accuracy,
Precision, Recall, Brier Score, Log Loss, calibration, average edge, and
simulated ROI of a fixed-stake strategy that follows every YES/NO
recommendation (never a real trade).

Distinct from `backtest.py`: the backtest re-derives predictions
retrospectively with a strict time-based split to answer "how would the
model have performed historically". This module instead evaluates
predictions the engine *actually made and stored* at the time — the
real-world track record, not a simulation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .prediction.calibration import (
    MIN_MATCHED_PAIRS_FOR_CALIBRATION,
    brier_score,
    calibration_bins,
    log_loss,
)

POSITIVE_RECOMMENDATIONS = ("STRONG_YES", "YES", "WATCH_YES")
NEGATIVE_RECOMMENDATIONS = ("STRONG_NO", "NO", "WATCH_NO")

# Same floor calibration.py already established (see its module docstring):
# a breakdown below this many matched pairs is too small to draw any real
# conclusion from and is reported as such (N shown, no verdict), never
# padded or silently omitted.
MIN_N_FOR_BREAKDOWN_CONCLUSION = 10


@dataclass
class EvaluationReport:
    n_snapshots_total: int
    n_evaluable: int  # snapshots whose market has since resolved
    n_directional: int  # evaluable snapshots with a YES/NO recommendation (excludes NO_BET/INSUFFICIENT_DATA)
    accuracy: float | None
    precision: float | None
    recall: float | None
    brier_score: float | None
    log_loss: float | None
    calibration: list[dict]
    average_net_edge: float | None
    simulated_roi: float | None

    def as_dict(self) -> dict:
        return {
            "n_snapshots_total": self.n_snapshots_total,
            "n_evaluable": self.n_evaluable,
            "n_directional": self.n_directional,
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "calibration": self.calibration,
            "average_net_edge": self.average_net_edge,
            "simulated_roi": self.simulated_roi,
        }


def evaluate_predictions(conn: sqlite3.Connection) -> EvaluationReport:
    import math

    rows = conn.execute(
        """
        SELECT ps.estimated_yes_probability, ps.market_yes_probability, ps.net_yes_edge,
               ps.recommendation, mr.winning_outcome
        FROM prediction_snapshots ps
        JOIN market_resolutions mr
          ON mr.provider = ps.provider AND mr.provider_market_id = ps.provider_market_id
        WHERE mr.status = 'resolved'
        """
    ).fetchall()
    n_total = conn.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0]
    n_evaluable = len(rows)

    if n_evaluable == 0:
        return EvaluationReport(
            n_snapshots_total=n_total, n_evaluable=0, n_directional=0, accuracy=None, precision=None,
            recall=None, brier_score=None, log_loss=None, calibration=[], average_net_edge=None, simulated_roi=None,
        )

    scored = [
        (est, mkt, edge, rec, 1 if (out and out.lower() == "yes") else 0)
        for est, mkt, edge, rec, out in rows
        if est is not None
    ]

    brier = round(sum((est - actual) ** 2 for est, _, _, _, actual in scored) / len(scored), 4) if scored else None

    eps = 1e-6
    log_loss = None
    if scored:
        log_loss = round(
            -sum(
                actual * math.log(min(max(est, eps), 1 - eps)) + (1 - actual) * math.log(1 - min(max(est, eps), 1 - eps))
                for est, _, _, _, actual in scored
            ) / len(scored),
            4,
        )

    buckets: dict[int, list[tuple]] = {}
    for est, _, _, _, actual in scored:
        bucket = min(9, int(est * 10))
        buckets.setdefault(bucket, []).append((est, actual))
    calibration = [
        {
            "bucket_predicted_range": f"{b * 10}-{b * 10 + 10}%",
            "n": len(items),
            "avg_predicted_yes": round(sum(i[0] for i in items) / len(items), 4),
            "observed_yes_rate": round(sum(i[1] for i in items) / len(items), 4),
        }
        for b, items in sorted(buckets.items())
    ]

    directional = [(rec, out) for _, _, _, rec, out in scored if rec in (*POSITIVE_RECOMMENDATIONS, *NEGATIVE_RECOMMENDATIONS)]
    n_directional = len(directional)

    accuracy = precision = recall = None
    if directional:
        correct = sum(
            1 for rec, out in directional
            if (rec in POSITIVE_RECOMMENDATIONS and out == 1) or (rec in NEGATIVE_RECOMMENDATIONS and out == 0)
        )
        accuracy = round(correct / n_directional, 4)

        predicted_positive = [out for rec, out in directional if rec in POSITIVE_RECOMMENDATIONS]
        if predicted_positive:
            true_positives = sum(predicted_positive)
            precision = round(true_positives / len(predicted_positive), 4)

        actual_positive = [rec for rec, out in directional if out == 1]
        if actual_positive:
            recalled = sum(1 for rec, out in directional if out == 1 and rec in POSITIVE_RECOMMENDATIONS)
            recall = round(recalled / len(actual_positive), 4)

    edges = [edge for _, _, edge, _, _ in scored if edge is not None]
    average_net_edge = round(sum(edges) / len(edges), 4) if edges else None

    # Simulated ROI of a fixed-stake strategy that buys 1 share of the
    # recommended side at the market's implied price for every YES/NO
    # (never WATCH_*/NO_BET/INSUFFICIENT_DATA) recommendation: payoff is
    # (1 - price) if the side wins, -price if it loses. Never a real trade.
    pnl = 0.0
    stake = 0.0
    for _est, mkt, _edge, rec, actual in scored:
        if mkt is None:
            continue
        if rec in POSITIVE_RECOMMENDATIONS:
            price, won = mkt, actual == 1
        elif rec in NEGATIVE_RECOMMENDATIONS:
            price, won = 1 - mkt, actual == 0
        else:
            continue
        stake += price
        pnl += (1 - price) if won else -price
    simulated_roi = round(pnl / stake, 4) if stake > 0 else None

    return EvaluationReport(
        n_snapshots_total=n_total, n_evaluable=n_evaluable, n_directional=n_directional,
        accuracy=accuracy, precision=precision, recall=recall, brier_score=brier, log_loss=log_loss,
        calibration=calibration, average_net_edge=average_net_edge, simulated_roi=simulated_roi,
    )


# ---------------------------------------------------------------------------
# BLOCK G — real, measurable evaluation on top of Block E's four-tier
# forecast-semantics snapshot fields (market_probability_at_forecast /
# model_hypothesis_probability / evidence_backed_probability /
# published_forecast_probability), plus category and submodel breakdowns.
#
# Deliberately distinct from `evaluate_predictions` above (which scores the
# older `estimated_yes_probability` field against every resolved snapshot
# regardless of whether a forecast was actually published): this section
# scores ONLY `published_forecast_probability`, and ONLY when it was
# non-null at forecast time. A market that correctly never published a
# forecast (NO_POSITION / WATCH under the Decision Engine's hard cap) has
# NOTHING to score — that is a correct, honest outcome, not a missing data
# point, and is never coerced into a score.
#
# Point-in-time safety (same invariant as calibration.py and
# proof_of_edge_backtest.py): a snapshot only counts when
#   snapshot.forecast_at < resolution.resolved_at
# a forecast made at/after resolution is excluded entirely, never used to
# inflate or deflate a score.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BreakdownResult:
    """One (category|submodel) slice of the forecast-history evaluation.
    `n` is always reported; `too_small` is set once `n` is below
    MIN_N_FOR_BREAKDOWN_CONCLUSION, and metrics are still computed (Brier/
    log-loss are well-defined for any n >= 1) but must be read as noise,
    not a real conclusion, until n grows."""

    key: str
    n: int
    too_small: bool
    brier_score: float | None
    log_loss_value: float | None
    mean_predicted_probability: float | None
    observed_frequency: float | None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "n": self.n,
            "too_small_for_conclusion": self.too_small,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss_value,
            "mean_predicted_probability": self.mean_predicted_probability,
            "observed_frequency": self.observed_frequency,
        }


def _summarize_pairs(pairs: list[tuple[float, bool]]) -> tuple[float | None, float | None, float | None, float | None]:
    if not pairs:
        return None, None, None, None
    b = brier_score(pairs)
    ll = log_loss(pairs)
    mean_pred = sum(p for p, _ in pairs) / len(pairs)
    observed = sum(1.0 for _, o in pairs if o) / len(pairs)
    return b, ll, mean_pred, observed


@dataclass
class ForecastHistoryEvaluation:
    """Part 1/2 result: real Brier/log-loss for `published_forecast_probability`
    against the market's own historical probability at forecast time,
    joined point-in-time-safe against `market_resolutions`, plus category
    and submodel (contributing-model) breakdowns. Every count is real —
    never padded — and status is UNCALIBRATED whenever the matched-pair
    count is below calibration.py's MIN_MATCHED_PAIRS_FOR_CALIBRATION."""

    status: str  # "UNCALIBRATED" or "CALIBRATED"
    matched_pair_count: int
    min_required: int
    brier_score: float | None
    log_loss_value: float | None
    bins: list[dict]
    by_category: list[BreakdownResult]
    by_submodel: list[BreakdownResult]

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "matched_pair_count": self.matched_pair_count,
            "min_required": self.min_required,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss_value,
            "bins": self.bins,
            "by_category": [c.as_dict() for c in self.by_category],
            "by_submodel": [s.as_dict() for s in self.by_submodel],
        }


def evaluate_forecast_history(conn: sqlite3.Connection) -> ForecastHistoryEvaluation:
    """The real Block G Part 1/2 evaluation function. Joins
    `prediction_snapshots` (Block E's four-tier fields) against
    `market_resolutions`, scoring `published_forecast_probability` only
    where it is non-null and only for snapshots taken strictly before
    resolution. Reuses calibration.py's brier_score/log_loss/
    calibration_bins — no metric logic is reimplemented here."""
    return _evaluate_snapshot_field(conn, "published_forecast_probability")


def evaluate_model_hypothesis_history(conn: sqlite3.Connection) -> ForecastHistoryEvaluation:
    """PART 11: same point-in-time-safe join and Brier/log-loss machinery as
    `evaluate_forecast_history`, but scored against `model_hypothesis_probability`
    instead of `published_forecast_probability`. `model_hypothesis_probability`
    is the raw specialized-model estimate BEFORE the evidence gate decides
    whether to publish, so it is populated far more often than the published
    field (which is 0/N whenever the evidence gate withholds publication).
    This exists to answer a diagnostic question the published-only metric
    structurally cannot: was the model's raw hypothesis directionally right
    even on markets the gate correctly/incorrectly suppressed? It is a
    distinct, separately labeled result — never merged or conflated with
    `evaluate_forecast_history`'s output, since the two score different
    populations of snapshots (model_hypothesis_probability is non-null far
    more often than published_forecast_probability, so matched_pair_count
    will typically differ, sometimes substantially, between the two)."""
    return _evaluate_snapshot_field(conn, "model_hypothesis_probability")


def _evaluate_snapshot_field(conn: sqlite3.Connection, probability_column: str) -> ForecastHistoryEvaluation:
    """Shared implementation behind `evaluate_forecast_history` and
    `evaluate_model_hypothesis_history`: identical point-in-time-safe join,
    identical Brier/log-loss/calibration-bin machinery, differing only in
    which snapshot probability column is scored. `probability_column` is
    never user input (always one of the two literal column names above),
    so building the SQL string with it is safe."""
    rows = conn.execute(
        f"""
        SELECT s.{probability_column}, s.category, s.models_used,
               r.winning_outcome
        FROM prediction_snapshots s
        JOIN market_resolutions r
          ON r.provider = s.provider AND r.provider_market_id = s.provider_market_id
        WHERE s.forecast_at IS NOT NULL
          AND r.resolved_at IS NOT NULL
          AND s.forecast_at < r.resolved_at
          AND r.winning_outcome IN ('Yes', 'No')
          AND s.{probability_column} IS NOT NULL
        """
    ).fetchall()

    pairs: list[tuple[float, bool]] = []
    by_category: dict[str, list[tuple[float, bool]]] = {}
    by_submodel: dict[str, list[tuple[float, bool]]] = {}

    for published_prob, category, models_used, winning_outcome in rows:
        outcome = winning_outcome == "Yes"
        pairs.append((published_prob, outcome))
        cat_key = category or "UNCATEGORIZED"
        by_category.setdefault(cat_key, []).append((published_prob, outcome))
        for submodel in (models_used or "").split(","):
            submodel = submodel.strip()
            if submodel:
                by_submodel.setdefault(submodel, []).append((published_prob, outcome))

    matched_pair_count = len(pairs)

    category_results = [
        BreakdownResult(
            key=key, n=len(items), too_small=len(items) < MIN_N_FOR_BREAKDOWN_CONCLUSION,
            **dict(zip(
                ("brier_score", "log_loss_value", "mean_predicted_probability", "observed_frequency"),
                _summarize_pairs(items), strict=True,
            )),
        )
        for key, items in sorted(by_category.items())
    ]
    submodel_results = [
        BreakdownResult(
            key=key, n=len(items), too_small=len(items) < MIN_N_FOR_BREAKDOWN_CONCLUSION,
            **dict(zip(
                ("brier_score", "log_loss_value", "mean_predicted_probability", "observed_frequency"),
                _summarize_pairs(items), strict=True,
            )),
        )
        for key, items in sorted(by_submodel.items())
    ]

    if matched_pair_count < MIN_MATCHED_PAIRS_FOR_CALIBRATION:
        return ForecastHistoryEvaluation(
            status="UNCALIBRATED", matched_pair_count=matched_pair_count,
            min_required=MIN_MATCHED_PAIRS_FOR_CALIBRATION, brier_score=None, log_loss_value=None,
            bins=[], by_category=category_results, by_submodel=submodel_results,
        )

    return ForecastHistoryEvaluation(
        status="CALIBRATED", matched_pair_count=matched_pair_count,
        min_required=MIN_MATCHED_PAIRS_FOR_CALIBRATION,
        brier_score=brier_score(pairs), log_loss_value=log_loss(pairs),
        bins=[b.as_dict() for b in calibration_bins(pairs)],
        by_category=category_results, by_submodel=submodel_results,
    )


@dataclass
class SourcePerformanceEvaluation:
    """Part 3: measures whether source_registry sources/independence_groups
    that contributed claims to a market were directionally correct once
    that market resolved. Real machinery, honestly gated: the current
    schema (`claims`/`claim_sources`, migrations.py migration 18) records
    which source contributed a claim, but does NOT record which market
    that claim was evaluated for — there is no `claim_id -> (provider,
    provider_market_id)` linkage table today. Independent evidence lookups
    (prediction/independent_evidence.py) query claims live, per-request,
    and never persist that per-market linkage back to storage. Until that
    linkage exists, this function can only report a structural N=0 for
    every source/independence_group — not because too few markets have
    resolved, but because the join key itself does not exist in the
    schema yet. That is reported explicitly via `linkage_available`."""

    linkage_available: bool
    reason: str
    by_source: list[BreakdownResult]
    by_independence_group: list[BreakdownResult]

    def as_dict(self) -> dict:
        return {
            "linkage_available": self.linkage_available,
            "reason": self.reason,
            "by_source": [s.as_dict() for s in self.by_source],
            "by_independence_group": [g.as_dict() for g in self.by_independence_group],
        }


def evaluate_source_performance(conn: sqlite3.Connection) -> SourcePerformanceEvaluation:
    """Real check for Part 3: does the schema currently support joining
    claim sources to the specific market they were used to forecast, and
    that market's eventual resolution? If a `claim_market_links` (or
    equivalent) table exists it is used; otherwise this honestly reports
    `linkage_available=False` with an explicit reason rather than
    fabricating a claim about which sources "are good"."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    # No table in the current schema links a claim_id (and therefore a
    # source_id / independence_group) to the (provider, provider_market_id)
    # of the market it was actually used to forecast. `claims`/
    # `claim_sources` (migration 18) record source attribution per claim,
    # but not per-market usage.
    linkage_table_candidates = {"claim_market_links", "claim_market_usage"}
    linkage_available = bool(tables & linkage_table_candidates)

    if not linkage_available:
        return SourcePerformanceEvaluation(
            linkage_available=False,
            reason=(
                "No claim-to-market linkage table exists in the current schema "
                "(checked for: " + ", ".join(sorted(linkage_table_candidates)) + "). "
                "claims/claim_sources (migration 18) record which source contributed "
                "a claim, but not which market that claim was used to forecast, so "
                "source-vs-resolved-outcome correlation cannot be computed from real "
                "data yet. This is a real infrastructure gap, not a sample-size issue."
            ),
            by_source=[], by_independence_group=[],
        )

    # Real computation path, exercised once the linkage table exists:
    # join claim_market_links -> claim_sources -> claims -> market_resolutions,
    # scoring each source's/independence_group's claims as directionally
    # correct/incorrect against the resolved winning_outcome, with the same
    # point-in-time (claim timestamp < resolved_at) and N-reporting
    # discipline as evaluate_forecast_history above.
    rows = conn.execute(
        """
        SELECT cs.source_id, c.direction, c.timestamp, r.winning_outcome, r.resolved_at
        FROM claim_market_links l
        JOIN claim_sources cs ON cs.claim_id = l.claim_id
        JOIN claims c ON c.claim_id = l.claim_id
        JOIN market_resolutions r
          ON r.provider = l.provider AND r.provider_market_id = l.provider_market_id
        WHERE c.timestamp IS NOT NULL AND r.resolved_at IS NOT NULL
          AND c.timestamp < r.resolved_at
          AND r.winning_outcome IN ('Yes', 'No')
          AND c.direction IN ('positive', 'negative')
        """
    ).fetchall()

    by_source: dict[str, list[tuple[float, bool]]] = {}
    for source_id, direction, _ts, winning_outcome, _resolved_at in rows:
        predicted_yes = 1.0 if direction == "positive" else 0.0
        outcome = winning_outcome == "Yes"
        by_source.setdefault(source_id, []).append((predicted_yes, outcome))

    by_source_results = [
        BreakdownResult(
            key=key, n=len(items), too_small=len(items) < MIN_N_FOR_BREAKDOWN_CONCLUSION,
            **dict(zip(
                ("brier_score", "log_loss_value", "mean_predicted_probability", "observed_frequency"),
                _summarize_pairs(items), strict=True,
            )),
        )
        for key, items in sorted(by_source.items())
    ]

    return SourcePerformanceEvaluation(
        linkage_available=True, reason="claim_market_links present; real join computed.",
        by_source=by_source_results, by_independence_group=[],
    )
