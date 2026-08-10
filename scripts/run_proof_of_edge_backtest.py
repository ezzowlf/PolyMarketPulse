"""CLI wrapper for the point-in-time-safe Proof-of-Edge backtest
(src/polymarketpulse/proof_of_edge_backtest.py). Read-only: computes and
prints Brier score / log loss / calibration / sample sizes for PMP's real
independent_probability vs. Polymarket's own real historical price, split by
domain. Does not mutate the database (compute_prediction as used here is
never given a real market_id write path beyond what it already touches for
normal live calls -- see note below) and performs NO post-hoc threshold
tuning.

Usage:
    python scripts/run_proof_of_edge_backtest.py [--db PATH] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarketpulse.prediction.calibration import brier_score, log_loss
from polymarketpulse.proof_of_edge_backtest import run_proof_of_edge_backtest
from polymarketpulse.storage import Storage

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "polymarketpulse.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB))
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    store = Storage(Path(args.db))
    conn = store.connection

    cases = run_proof_of_edge_backtest(conn)
    store.connection.close()

    excluded = [c for c in cases if c.excluded_reason is not None]
    evaluated = [c for c in cases if c.excluded_reason is None]
    pmp_scored = [c for c in evaluated if c.independent_probability is not None]
    pmp_no_forecast = [c for c in evaluated if c.independent_probability is None]

    print(f"Total eligible (real backfilled price history): {len(cases)}")
    print(f"Excluded (macro/quant or no pre-resolution point): {len(excluded)}")
    exclusion_counts: dict[str, int] = {}
    for c in excluded:
        exclusion_counts[c.excluded_reason] = exclusion_counts.get(c.excluded_reason, 0) + 1
    for reason, n in exclusion_counts.items():
        print(f"  - {reason}: {n}")
    print(f"Evaluated (real as_of-safe compute_prediction run): {len(evaluated)}")
    print(f"  PMP produced a real independent_probability: {len(pmp_scored)}")
    print(f"  PMP returned NO_FORECAST (honest, valid outcome): {len(pmp_no_forecast)}")

    def _report(cases_subset, label):
        if not cases_subset:
            print(f"\n[{label}] N=0 -- no cases")
            return None
        pmp_pairs = [(c.independent_probability, c.outcome_yes) for c in cases_subset if c.independent_probability is not None]
        poly_pairs = [(c.benchmark_price, c.outcome_yes) for c in cases_subset]
        print(f"\n[{label}] N_total={len(cases_subset)}, N_pmp_scored={len(pmp_pairs)}")
        if len(pmp_pairs) >= 1:
            print(f"  PMP     Brier={brier_score(pmp_pairs):.4f}  LogLoss={log_loss(pmp_pairs):.4f}")
        else:
            print("  PMP     N/A (no scored cases)")
        print(f"  Polymarket(hist) Brier={brier_score(poly_pairs):.4f}  LogLoss={log_loss(poly_pairs):.4f}")
        if len(pmp_pairs) < 10:
            print(f"  ** N={len(pmp_pairs)} < 10 -- TOO SMALL for a real conclusion **")
        return {
            "n_total": len(cases_subset),
            "n_pmp_scored": len(pmp_pairs),
            "pmp_brier": brier_score(pmp_pairs) if pmp_pairs else None,
            "pmp_log_loss": log_loss(pmp_pairs) if pmp_pairs else None,
            "polymarket_brier": brier_score(poly_pairs),
            "polymarket_log_loss": log_loss(poly_pairs),
        }

    overall = _report(evaluated, "OVERALL")

    domains = sorted({c.domain or c.category or "UNKNOWN" for c in evaluated})
    by_domain = {}
    for d in domains:
        subset = [c for c in evaluated if (c.domain or c.category or "UNKNOWN") == d]
        by_domain[d] = _report(subset, f"DOMAIN={d}")

    if args.json:
        out = {
            "overall": overall,
            "by_domain": by_domain,
            "cases": [
                {
                    "market_id": c.market_id, "question": c.question, "domain": c.domain,
                    "category": c.category, "event_type": c.event_type,
                    "resolved_at": c.resolved_at.isoformat(), "forecast_time": c.forecast_time.isoformat(),
                    "benchmark_price": c.benchmark_price, "outcome_yes": c.outcome_yes,
                    "independent_probability": c.independent_probability,
                    "forecast_status": c.forecast_status, "forecast_maturity": c.forecast_maturity,
                    "excluded_reason": c.excluded_reason,
                }
                for c in cases
            ],
        }
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
