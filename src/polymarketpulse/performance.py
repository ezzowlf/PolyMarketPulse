from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PerformanceSummary:
    """Aggregate simulated performance across resolved signals. Every
    resolved signal contributes a 1.0-virtual-unit position; never real
    money, never a portfolio recommendation. The equity curve is a simple
    chronological running sum of each signal's simulated P&L — it does not
    model concurrent capital allocation or compounding."""

    evaluated_count: int
    cumulative_return: float | None
    average_return_per_signal: float | None
    max_drawdown: float | None
    max_equity: float | None
    win_rate: float | None
    average_hold_hours: float | None
    equity_curve: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "evaluated_count": self.evaluated_count,
            "cumulative_return": self.cumulative_return,
            "average_return_per_signal": self.average_return_per_signal,
            "max_drawdown": self.max_drawdown,
            "max_equity": self.max_equity,
            "win_rate": self.win_rate,
            "average_hold_hours": self.average_hold_hours,
            "equity_curve": self.equity_curve,
        }


def compute_performance(conn: sqlite3.Connection) -> PerformanceSummary:
    rows = conn.execute(
        """
        SELECT se.evaluated_at, se.simulated_pnl_per_unit, se.correct, se.hold_duration_hours
        FROM signal_evaluations se
        WHERE se.simulated_pnl_per_unit IS NOT NULL
        ORDER BY se.evaluated_at ASC
        """
    ).fetchall()

    if not rows:
        return PerformanceSummary(
            evaluated_count=0,
            cumulative_return=None,
            average_return_per_signal=None,
            max_drawdown=None,
            max_equity=None,
            win_rate=None,
            average_hold_hours=None,
        )

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    curve: list[dict] = []
    for evaluated_at, pnl, _correct, _hold in rows:
        equity += pnl
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        curve.append({"evaluated_at": evaluated_at, "equity": round(equity, 4)})

    pnls = [r[1] for r in rows]
    corrects = [r[2] for r in rows if r[2] is not None]
    holds = [r[3] for r in rows if r[3] is not None]

    return PerformanceSummary(
        evaluated_count=len(rows),
        cumulative_return=round(equity, 4),
        average_return_per_signal=round(sum(pnls) / len(pnls), 4),
        max_drawdown=round(max_drawdown, 4),
        max_equity=round(peak, 4),
        win_rate=(sum(corrects) / len(corrects)) if corrects else None,
        average_hold_hours=(sum(holds) / len(holds)) if holds else None,
        equity_curve=curve,
    )
