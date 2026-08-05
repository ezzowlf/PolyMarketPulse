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

POSITIVE_RECOMMENDATIONS = ("STRONG_YES", "YES", "WATCH_YES")
NEGATIVE_RECOMMENDATIONS = ("STRONG_NO", "NO", "WATCH_NO")


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
