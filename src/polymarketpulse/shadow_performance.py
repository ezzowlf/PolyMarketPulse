"""Shadow-trading performance evaluation and per-submodel comparison.
Reads closed `shadow_trades` (and `prediction_snapshots` joined against
`market_resolutions` for the submodel comparison) — never makes network
calls, never touches real money. Mirrors evaluation.py's metric choices
(Brier, hit rate, ROI) but scoped to the *qualified* shadow trades rather
than every prediction snapshot, and adds the breakdowns and drawdown/equity
figures the shadow layer specifically needs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field


@dataclass
class ShadowPerformanceReport:
    n_candidates: int
    n_skipped: int
    n_active: int
    n_closed: int
    hit_rate: float | None
    brier_score: float | None
    total_pnl: float | None
    average_roi: float | None
    max_drawdown: float | None
    average_holding_hours: float | None
    average_entry_edge: float | None
    equity_curve: list[dict] = field(default_factory=list)
    breakdown_by_confidence: list[dict] = field(default_factory=list)
    breakdown_by_opportunity_score: list[dict] = field(default_factory=list)
    breakdown_by_deadline_phase: list[dict] = field(default_factory=list)
    breakdown_by_direction: list[dict] = field(default_factory=list)
    most_common_blockers: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "n_candidates": self.n_candidates, "n_skipped": self.n_skipped, "n_active": self.n_active,
            "n_closed": self.n_closed, "hit_rate": self.hit_rate, "brier_score": self.brier_score,
            "total_pnl": self.total_pnl, "average_roi": self.average_roi, "max_drawdown": self.max_drawdown,
            "average_holding_hours": self.average_holding_hours, "average_entry_edge": self.average_entry_edge,
            "equity_curve": self.equity_curve, "breakdown_by_confidence": self.breakdown_by_confidence,
            "breakdown_by_opportunity_score": self.breakdown_by_opportunity_score,
            "breakdown_by_deadline_phase": self.breakdown_by_deadline_phase,
            "breakdown_by_direction": self.breakdown_by_direction,
            "most_common_blockers": self.most_common_blockers,
        }


def _bucket_by(rows: list[dict], key: str, buckets: list[tuple[str, float, float]]) -> list[dict]:
    """`buckets` is [(label, low, high), ...]; returns per-bucket hit rate/count/avg pnl."""
    result = []
    for label, low, high in buckets:
        in_bucket = [r for r in rows if r.get(key) is not None and low <= r[key] < high]
        if not in_bucket:
            result.append({"bucket": label, "n": 0, "hit_rate": None, "average_pnl": None})
            continue
        wins = sum(1 for r in in_bucket if r.get("final_outcome") == r.get("direction"))
        avg_pnl = sum(r.get("simulated_pnl") or 0.0 for r in in_bucket) / len(in_bucket)
        result.append({"bucket": label, "n": len(in_bucket), "hit_rate": round(wins / len(in_bucket), 4), "average_pnl": round(avg_pnl, 4)})
    return result


def compute_shadow_performance(conn: sqlite3.Connection) -> ShadowPerformanceReport:
    all_rows_raw = conn.execute("SELECT * FROM shadow_trades ORDER BY created_at ASC").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM shadow_trades LIMIT 0").description]
    all_rows = [dict(zip(cols, r, strict=True)) for r in all_rows_raw]

    n_candidates = sum(1 for r in all_rows if r["status"] in ("candidate", "active", "closed"))
    n_skipped = sum(1 for r in all_rows if r["status"] == "skipped")
    n_active = sum(1 for r in all_rows if r["status"] == "active")
    closed = [r for r in all_rows if r["status"] == "closed" and r.get("final_outcome") is not None]
    n_closed = len(closed)

    hit_rate = brier = total_pnl = average_roi = max_drawdown = average_holding_hours = average_entry_edge = None
    equity_curve: list[dict] = []

    if closed:
        wins = sum(1 for r in closed if r["direction"] == r["final_outcome"])
        hit_rate = round(wins / n_closed, 4)

        scored = [
            (r["independent_probability"] if r["direction"] == "YES" else 1 - r["independent_probability"], 1 if r["direction"] == r["final_outcome"] else 0)
            for r in closed if r.get("independent_probability") is not None
        ]
        if scored:
            brier = round(sum((p - actual) ** 2 for p, actual in scored) / len(scored), 4)

        pnls = [r["simulated_pnl"] for r in closed if r.get("simulated_pnl") is not None]
        total_pnl = round(sum(pnls), 4) if pnls else None
        rois = [r["roi"] for r in closed if r.get("roi") is not None]
        average_roi = round(sum(rois) / len(rois), 4) if rois else None
        drawdowns = [r["max_drawdown"] for r in closed if r.get("max_drawdown") is not None]
        max_drawdown = round(max(drawdowns), 4) if drawdowns else None
        holding = [r["holding_hours"] for r in closed if r.get("holding_hours") is not None]
        average_holding_hours = round(sum(holding) / len(holding), 2) if holding else None
        edges = [abs(r["expected_edge"]) for r in closed if r.get("expected_edge") is not None]
        average_entry_edge = round(sum(edges) / len(edges), 4) if edges else None

        running = 0.0
        for r in sorted(closed, key=lambda x: x["closed_at"] or ""):
            running += r.get("simulated_pnl") or 0.0
            equity_curve.append({"closed_at": r["closed_at"], "cumulative_pnl": round(running, 4)})

    breakdown_by_confidence = _bucket_by(closed, "confidence", [("<40", 0, 40), ("40-60", 40, 60), ("60-80", 60, 80), ("80-100", 80, 101)])
    breakdown_by_opportunity_score = _bucket_by(closed, "opportunity_score", [("<50", 0, 50), ("50-70", 50, 70), ("70-100", 70, 101)])

    deadline_phases = sorted({r["deadline_phase"] for r in closed if r.get("deadline_phase")})
    breakdown_by_deadline_phase = []
    for phase in deadline_phases:
        in_phase = [r for r in closed if r.get("deadline_phase") == phase]
        wins = sum(1 for r in in_phase if r["direction"] == r["final_outcome"])
        breakdown_by_deadline_phase.append({"phase": phase, "n": len(in_phase), "hit_rate": round(wins / len(in_phase), 4) if in_phase else None})

    breakdown_by_direction = []
    for direction in ("YES", "NO"):
        in_dir = [r for r in closed if r["direction"] == direction]
        if not in_dir:
            breakdown_by_direction.append({"direction": direction, "n": 0, "hit_rate": None})
            continue
        wins = sum(1 for r in in_dir if r["direction"] == r["final_outcome"])
        breakdown_by_direction.append({"direction": direction, "n": len(in_dir), "hit_rate": round(wins / len(in_dir), 4)})

    blocker_counts: dict[str, int] = {}
    for r in all_rows:
        if r["status"] != "skipped":
            continue
        for b in json.loads(r.get("blockers_json") or "[]"):
            blocker_counts[b] = blocker_counts.get(b, 0) + 1
    most_common_blockers = [{"blocker": b, "count": c} for b, c in sorted(blocker_counts.items(), key=lambda x: -x[1])[:10]]

    return ShadowPerformanceReport(
        n_candidates=n_candidates, n_skipped=n_skipped, n_active=n_active, n_closed=n_closed,
        hit_rate=hit_rate, brier_score=brier, total_pnl=total_pnl, average_roi=average_roi,
        max_drawdown=max_drawdown, average_holding_hours=average_holding_hours, average_entry_edge=average_entry_edge,
        equity_curve=equity_curve, breakdown_by_confidence=breakdown_by_confidence,
        breakdown_by_opportunity_score=breakdown_by_opportunity_score,
        breakdown_by_deadline_phase=breakdown_by_deadline_phase, breakdown_by_direction=breakdown_by_direction,
        most_common_blockers=most_common_blockers,
    )


@dataclass
class SubmodelComparisonEntry:
    name: str
    n_available: int
    n_evaluable: int
    brier_score: float | None
    hit_rate: float | None

    def as_dict(self) -> dict:
        return {
            "name": self.name, "n_available": self.n_available, "n_evaluable": self.n_evaluable,
            "brier_score": self.brier_score, "hit_rate": self.hit_rate,
        }


def compute_submodel_comparison(conn: sqlite3.Connection) -> list[SubmodelComparisonEntry]:
    """For each submodel name seen in stored `submodel_estimates_json`,
    computes Brier score and hit rate against the eventual resolution —
    letting individual submodels be judged separately from the blended
    ensemble result."""
    rows = conn.execute(
        """
        SELECT ps.submodel_estimates_json, mr.winning_outcome
        FROM prediction_snapshots ps
        JOIN market_resolutions mr ON mr.provider = ps.provider AND mr.provider_market_id = ps.provider_market_id
        WHERE mr.status = 'resolved' AND ps.submodel_estimates_json IS NOT NULL
        """
    ).fetchall()

    per_model: dict[str, list[tuple[float, int]]] = {}
    for submodel_json, winning_outcome in rows:
        actual = 1 if (winning_outcome and winning_outcome.lower() == "yes") else 0
        try:
            submodels = json.loads(submodel_json)
        except (TypeError, ValueError):
            continue
        for s in submodels:
            name = s.get("name")
            if not name:
                continue
            per_model.setdefault(name, [])
            if s.get("available") and s.get("estimated_yes_probability") is not None:
                per_model[name].append((s["estimated_yes_probability"], actual))

    entries = []
    for name, scored in sorted(per_model.items()):
        n_evaluable = len(scored)
        n_available = len(scored)  # only "available" entries were appended above
        brier = round(sum((p - a) ** 2 for p, a in scored) / n_evaluable, 4) if n_evaluable else None
        hits = sum(1 for p, a in scored if (p >= 0.5) == bool(a))
        hit_rate = round(hits / n_evaluable, 4) if n_evaluable else None
        entries.append(SubmodelComparisonEntry(name=name, n_available=n_available, n_evaluable=n_evaluable, brier_score=brier, hit_rate=hit_rate))
    return entries
