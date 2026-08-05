from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from .prediction import _recommendation

BACKTEST_VERSION = "v1"


@dataclass
class BacktestCase:
    market_id: str
    category: str | None
    resolved_at: str
    market_yes_price: float | None
    predicted_yes: float
    actual_yes: int
    train_sample_size: int
    recommendation: str
    net_yes_edge: float | None


@dataclass
class BacktestReport:
    n_total_resolved: int
    n_evaluated: int
    n_skipped: int
    skipped_reasons: dict[str, int]
    brier_score: float | None
    log_loss: float | None
    calibration: list[dict]
    cumulative_return: float
    max_drawdown: float
    performance_yes: dict
    performance_no: dict
    performance_by_category: dict[str, dict]
    cases: list[BacktestCase] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "backtest_version": BACKTEST_VERSION,
            "n_total_resolved": self.n_total_resolved,
            "n_evaluated": self.n_evaluated,
            "n_skipped": self.n_skipped,
            "skipped_reasons": self.skipped_reasons,
            "brier_score": self.brier_score,
            "log_loss": self.log_loss,
            "calibration": self.calibration,
            "cumulative_return": self.cumulative_return,
            "max_drawdown": self.max_drawdown,
            "performance_yes": self.performance_yes,
            "performance_no": self.performance_no,
            "performance_by_category": self.performance_by_category,
        }


def _blend(observed_yes_rate: float, market_yes: float | None, sample_size: int) -> float:
    historical_weight = min(0.6, sample_size / 50)
    if market_yes is None:
        return observed_yes_rate
    return round(historical_weight * observed_yes_rate + (1 - historical_weight) * market_yes, 4)


def _price_before(conn: sqlite3.Connection, market_id: str, cutoff_iso: str) -> float | None:
    row = conn.execute(
        "SELECT yes_price FROM market_snapshots WHERE market_id = ? AND captured_at <= ? "
        "ORDER BY captured_at DESC LIMIT 1",
        (market_id, cutoff_iso),
    ).fetchone()
    if row is not None and row[0] is not None:
        return row[0]
    row = conn.execute(
        "SELECT yes_price FROM market_snapshots WHERE market_id = ? ORDER BY captured_at ASC LIMIT 1",
        (market_id,),
    ).fetchone()
    return row[0] if row is not None else None


def run_backtest(
    conn: sqlite3.Connection, category: str | None = None, min_train_size: int = 5
) -> BacktestReport:
    """Walk-forward backtest of the prediction engine's base-rate blend.

    For every resolved market, ordered by `resolved_at`, only markets that
    resolved *strictly earlier* are ever used to build the historical base
    rate — this is the time-based split that rules out look-ahead bias. A
    case is skipped (not silently dropped) whenever fewer than
    `min_train_size` earlier resolved markets exist in the same category, or
    no market price snapshot is available for it.
    """
    rows = conn.execute(
        """
        SELECT m.market_id, m.category, mr.resolved_at, mr.winning_outcome
        FROM market_resolutions mr
        JOIN markets m ON m.provider = mr.provider AND m.provider_market_id = mr.provider_market_id
        WHERE mr.status = 'resolved' AND mr.resolved_at IS NOT NULL
        ORDER BY mr.resolved_at ASC
        """
    ).fetchall()
    if category:
        rows = [r for r in rows if r[1] == category]

    n_total = len(rows)
    skipped_reasons: dict[str, int] = {}
    cases: list[BacktestCase] = []

    for idx, (market_id, cat, resolved_at, winning_outcome) in enumerate(rows):
        earlier = [r for r in rows[:idx] if r[1] == cat]
        train_size = len(earlier)
        if train_size < min_train_size:
            skipped_reasons["zu_wenig_trainingsdaten"] = skipped_reasons.get("zu_wenig_trainingsdaten", 0) + 1
            continue

        market_yes = _price_before(conn, market_id, resolved_at)
        if market_yes is None:
            skipped_reasons["kein_marktpreis_verfuegbar"] = skipped_reasons.get("kein_marktpreis_verfuegbar", 0) + 1
            continue

        yes_count = sum(1 for r in earlier if r[3] and r[3].lower() == "yes")
        observed_yes_rate = yes_count / train_size
        predicted_yes = _blend(observed_yes_rate, market_yes, train_size)

        gross_edge = predicted_yes - market_yes
        cost_haircut = 0.02
        net_edge = gross_edge - cost_haircut if gross_edge > 0 else gross_edge + cost_haircut
        if abs(net_edge) < 1e-9:
            net_edge = 0.0

        # Confidence proxy mirrors prediction.py's shape without requiring
        # the full data-quality inputs a backtest doesn't have.
        confidence = min(100.0, min(30.0, train_size * 3.0) + 50.0)
        recommendation = _recommendation(net_edge, confidence, train_size)

        actual_yes = 1 if winning_outcome and winning_outcome.lower() == "yes" else 0
        cases.append(
            BacktestCase(
                market_id=market_id, category=cat, resolved_at=resolved_at, market_yes_price=market_yes,
                predicted_yes=predicted_yes, actual_yes=actual_yes, train_sample_size=train_size,
                recommendation=recommendation, net_yes_edge=net_edge,
            )
        )

    n_evaluated = len(cases)
    if n_evaluated == 0:
        return BacktestReport(
            n_total_resolved=n_total, n_evaluated=0, n_skipped=n_total, skipped_reasons=skipped_reasons,
            brier_score=None, log_loss=None, calibration=[], cumulative_return=0.0, max_drawdown=0.0,
            performance_yes={}, performance_no={}, performance_by_category={},
        )

    brier = sum((c.predicted_yes - c.actual_yes) ** 2 for c in cases) / n_evaluated

    eps = 1e-6
    log_loss = -sum(
        c.actual_yes * math.log(min(max(c.predicted_yes, eps), 1 - eps))
        + (1 - c.actual_yes) * math.log(1 - min(max(c.predicted_yes, eps), 1 - eps))
        for c in cases
    ) / n_evaluated

    buckets: dict[int, list[BacktestCase]] = {}
    for c in cases:
        bucket = min(9, int(c.predicted_yes * 10))
        buckets.setdefault(bucket, []).append(c)
    calibration = [
        {
            "bucket_predicted_range": f"{b * 10}-{b * 10 + 10}%",
            "n": len(items),
            "avg_predicted_yes": round(sum(i.predicted_yes for i in items) / len(items), 4),
            "observed_yes_rate": round(sum(i.actual_yes for i in items) / len(items), 4),
        }
        for b, items in sorted(buckets.items())
    ]

    def _perf_for(selected: list[BacktestCase], is_yes_side: bool) -> dict:
        if not selected:
            return {"n_trades": 0, "cumulative_return": 0.0, "win_rate": None, "max_drawdown": 0.0}
        returns = []
        for c in selected:
            price = c.market_yes_price or 0.0
            returns.append((c.actual_yes - price) if is_yes_side else (price - c.actual_yes))
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        wins = 0
        for r in returns:
            cumulative += r
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
            if r > 0:
                wins += 1
        return {
            "n_trades": len(returns),
            "cumulative_return": round(cumulative, 4),
            "win_rate": round(wins / len(returns), 4),
            "max_drawdown": round(max_dd, 4),
        }

    yes_cases = [c for c in cases if c.recommendation in ("STRONG_YES", "YES", "WATCH_YES")]
    no_cases = [c for c in cases if c.recommendation in ("STRONG_NO", "NO", "WATCH_NO")]
    perf_yes = _perf_for(yes_cases, is_yes_side=True)
    perf_no = _perf_for(no_cases, is_yes_side=False)

    all_traded = [(c, True) for c in yes_cases] + [(c, False) for c in no_cases]
    all_traded.sort(key=lambda t: t[0].resolved_at)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for c, is_yes in all_traded:
        price = c.market_yes_price or 0.0
        r = (c.actual_yes - price) if is_yes else (price - c.actual_yes)
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    categories = sorted({c.category for c in cases if c.category})
    performance_by_category = {}
    for cat in categories:
        cat_cases = [c for c in cases if c.category == cat]
        cat_yes = [c for c in cat_cases if c.recommendation in ("STRONG_YES", "YES", "WATCH_YES")]
        cat_no = [c for c in cat_cases if c.recommendation in ("STRONG_NO", "NO", "WATCH_NO")]
        performance_by_category[cat] = {
            "n_evaluated": len(cat_cases),
            "brier_score": round(sum((c.predicted_yes - c.actual_yes) ** 2 for c in cat_cases) / len(cat_cases), 4),
            "yes": _perf_for(cat_yes, is_yes_side=True),
            "no": _perf_for(cat_no, is_yes_side=False),
        }

    return BacktestReport(
        n_total_resolved=n_total,
        n_evaluated=n_evaluated,
        n_skipped=n_total - n_evaluated,
        skipped_reasons=skipped_reasons,
        brier_score=round(brier, 4),
        log_loss=round(log_loss, 4),
        calibration=calibration,
        cumulative_return=round(cumulative, 4),
        max_drawdown=round(max_dd, 4),
        performance_yes=perf_yes,
        performance_no=perf_no,
        performance_by_category=performance_by_category,
        cases=cases,
    )
