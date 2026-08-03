from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field


@dataclass
class SignalStats:
    signal_count: int = 0
    evaluated_count: int = 0
    hit_rate: float | None = None
    average_signal_price: float | None = None
    average_simulated_return: float | None = None
    brier_score: float | None = None
    log_loss: float | None = None
    breakdown_by_type: dict = field(default_factory=dict)
    breakdown_by_provider: dict = field(default_factory=dict)
    breakdown_by_category: dict = field(default_factory=dict)
    breakdown_by_liquidity: dict = field(default_factory=dict)
    breakdown_by_time_to_resolution: dict = field(default_factory=dict)
    calibration_buckets: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "signal_count": self.signal_count,
            "evaluated_count": self.evaluated_count,
            "hit_rate": self.hit_rate,
            "average_signal_price": self.average_signal_price,
            "average_simulated_return": self.average_simulated_return,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "breakdown_by_type": self.breakdown_by_type,
            "breakdown_by_provider": self.breakdown_by_provider,
            "breakdown_by_category": self.breakdown_by_category,
            "breakdown_by_liquidity": self.breakdown_by_liquidity,
            "breakdown_by_time_to_resolution": self.breakdown_by_time_to_resolution,
            "calibration_buckets": self.calibration_buckets,
        }


def _breakdown(conn: sqlite3.Connection, group_expr: str) -> dict:
    rows = conn.execute(
        f"""
        SELECT {group_expr} AS bucket,
               COUNT(*) AS n,
               AVG(CASE WHEN se.correct IS NOT NULL THEN se.correct * 1.0 END) AS hit_rate,
               AVG(se.simulated_pnl_per_unit) AS avg_return
        FROM research_signals rs
        JOIN signal_evaluations se ON se.signal_id = rs.id
        LEFT JOIN markets m ON m.provider = rs.provider AND m.provider_market_id = rs.provider_market_id
        GROUP BY bucket
        """
    ).fetchall()
    return {
        row[0]: {"count": row[1], "hit_rate": row[2], "average_simulated_return": row[3]}
        for row in rows
        if row[0] is not None
    }


def compute_signal_stats(conn: sqlite3.Connection) -> SignalStats:
    """Statistics over *resolved* research signals only. `forecast_probability`
    (a genuine probability estimate, distinct from the research score) is
    required for Brier score / log loss — without it those fields stay None
    rather than silently treating the research score as a probability.
    """
    stats = SignalStats()
    stats.signal_count = conn.execute("SELECT COUNT(*) FROM research_signals").fetchone()[0]

    evaluated = conn.execute(
        """
        SELECT rs.forecast_probability, se.correct, se.simulated_pnl_per_unit, rs.origin_yes_price
        FROM research_signals rs
        JOIN signal_evaluations se ON se.signal_id = rs.id
        """
    ).fetchall()
    stats.evaluated_count = len(evaluated)

    if evaluated:
        correct_values = [row[1] for row in evaluated if row[1] is not None]
        if correct_values:
            stats.hit_rate = sum(correct_values) / len(correct_values)

        prices = [row[3] for row in evaluated if row[3] is not None]
        if prices:
            stats.average_signal_price = sum(prices) / len(prices)

        returns = [row[2] for row in evaluated if row[2] is not None]
        if returns:
            stats.average_simulated_return = sum(returns) / len(returns)

        forecasted = [(row[0], row[1]) for row in evaluated if row[0] is not None and row[1] is not None]
        if forecasted:
            n = len(forecasted)
            stats.brier_score = sum((p - outcome) ** 2 for p, outcome in forecasted) / n
            eps = 1e-9
            stats.log_loss = -sum(
                outcome * math.log(max(p, eps)) + (1 - outcome) * math.log(max(1 - p, eps))
                for p, outcome in forecasted
            ) / n

            buckets: dict[str, list[int]] = {}
            for p, outcome in forecasted:
                bucket = f"{int(p * 10) * 10}-{int(p * 10) * 10 + 10}%"
                buckets.setdefault(bucket, []).append(outcome)
            stats.calibration_buckets = {
                bucket: {"count": len(outcomes), "actual_rate": sum(outcomes) / len(outcomes)}
                for bucket, outcomes in sorted(buckets.items())
            }

    stats.breakdown_by_type = _breakdown(conn, "rs.signal_type")
    stats.breakdown_by_provider = _breakdown(conn, "rs.provider")
    stats.breakdown_by_category = _breakdown(conn, "m.category")
    stats.breakdown_by_liquidity = _signal_field_breakdown(
        conn,
        "CASE WHEN rs.origin_liquidity IS NULL THEN 'unknown' "
        "WHEN rs.origin_liquidity < 5000 THEN '<5k' "
        "WHEN rs.origin_liquidity < 25000 THEN '5k-25k' "
        "WHEN rs.origin_liquidity < 100000 THEN '25k-100k' "
        "ELSE '>=100k' END",
    )
    stats.breakdown_by_time_to_resolution = _signal_field_breakdown(
        conn,
        "CASE WHEN rs.origin_days_to_resolution IS NULL THEN 'unknown' "
        "WHEN rs.origin_days_to_resolution < 3 THEN '<3d' "
        "WHEN rs.origin_days_to_resolution < 14 THEN '3-14d' "
        "WHEN rs.origin_days_to_resolution < 90 THEN '14-90d' "
        "ELSE '>=90d' END",
    )
    return stats


def _signal_field_breakdown(conn: sqlite3.Connection, bucket_expr: str) -> dict:
    rows = conn.execute(
        f"""
        SELECT {bucket_expr} AS bucket,
               COUNT(*) AS n,
               AVG(CASE WHEN se.correct IS NOT NULL THEN se.correct * 1.0 END) AS hit_rate,
               AVG(se.simulated_pnl_per_unit) AS avg_return
        FROM research_signals rs
        JOIN signal_evaluations se ON se.signal_id = rs.id
        GROUP BY bucket
        """
    ).fetchall()
    return {
        row[0]: {"count": row[1], "hit_rate": row[2], "average_simulated_return": row[3]}
        for row in rows
    }
