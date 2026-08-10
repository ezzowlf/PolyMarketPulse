"""Point-in-time-safe Proof-of-Edge backtest: PMP's real independent_probability
vs. Polymarket's own real historical price, for resolved markets that have
real backfilled pre-resolution price history (migration 20,
polymarket_price_history table, see
scripts/backfill_polymarket_price_history.py).

This is a SEPARATE module from the existing `backtest.py` (a pre-existing,
actively-used, simpler walk-forward base-rate blend model wired into
cli.py/api.py) -- deliberately not touched or repurposed. This module
exercises the REAL production forecast pipeline
(`prediction.engine.compute_prediction`) via the `as_of` point-in-time-safety
parameter, which `backtest.py`'s blend model does not use at all.

Forecast-time selection rule (documented, applied consistently, no post-hoc
tuning): for each eligible market, pick the backfilled price point closest to
GAP_DAYS days before resolved_at, but never after resolved_at; if the
market's backfilled history is shorter than GAP_DAYS, use the EARLIEST
available point instead. This is an honest consequence of the CLOB
/prices-history endpoint's confirmed ~15-day max query-interval limit (see
providers/polymarket.py::fetch_price_history), not a chosen backtest design
constraint.

Point-in-time safety: `as_of=forecast_time` is threaded into
compute_prediction, which restricts history.py's comparable-market
candidates to markets resolved strictly before `as_of` and evidence.py's
linked news to items published at/before `as_of`. See
tests/test_as_of_point_in_time_safety.py for the explicit,
constructed-leak-would-change-the-result proof this actually works.

Known, honestly-documented simplification: liquidity/news_count/
data_quality_report_score are NOT reconstructable point-in-time in this
codebase's current schema (no historical snapshot of those specific fields
keyed by timestamp) and are passed as unavailable (0/0/None) rather than
leaking today's values. This slightly understates PMP's real historical
performance for markets where evidence/data-quality signals would genuinely
have been available at forecast_time -- a real, acknowledged limitation, not
a tuning choice.

Macro/quant markets (rate_cut/rate_hike/rate_hold/price_above/price_below)
are EXCLUDED: engine.py already disables the quant/macro specialized
submodels whenever as_of is not None (no cached historical FRED/CoinGecko
time-series exists in this codebase -- macro_observations is empty,
confirmed by inspection), so backtesting them would silently degrade to the
generic history+evidence path rather than a real macro/quant forecast.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .prediction.engine import compute_prediction

GAP_DAYS = 7

MACRO_QUANT_EVENT_TYPES = {"rate_cut", "rate_hike", "rate_hold", "price_above", "price_below"}


@dataclass
class ProofOfEdgeCase:
    market_id: str
    provider: str
    provider_market_id: str
    question: str
    category: str | None
    domain: str | None
    event_type: str | None
    resolved_at: datetime
    forecast_time: datetime
    benchmark_price: float  # Polymarket's real historical YES price at forecast_time
    outcome_yes: bool  # real eventual resolution
    independent_probability: float | None  # PMP's real output; None = NO_FORECAST
    forecast_status: str
    forecast_maturity: str | None
    excluded_reason: str | None = None


def _select_forecast_point(
    points: list[tuple[str, float]], resolved_at: datetime, gap_days: int = GAP_DAYS
) -> tuple[datetime, float] | None:
    parsed: list[tuple[datetime, float]] = []
    for ts, price in points:
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        if dt < resolved_at:
            parsed.append((dt, price))
    if not parsed:
        return None
    parsed.sort(key=lambda pair: pair[0])
    target = resolved_at - timedelta(days=gap_days)
    if parsed[0][0] >= target:
        return parsed[0]  # history shorter than gap_days -> earliest available point
    best = parsed[0]
    for dt, price in parsed:
        if dt <= target:
            best = (dt, price)
        else:
            break
    return best


def load_eligible_markets(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT DISTINCT m.market_id, m.provider, m.provider_market_id, m.question,
               m.category, m.classified_category, m.event_type,
               mr.resolved_at, mr.winning_outcome
        FROM market_resolutions mr
        JOIN markets m ON mr.provider = m.provider AND mr.provider_market_id = m.provider_market_id
        JOIN polymarket_price_history ph ON ph.market_id = m.market_id
        WHERE mr.status = 'resolved' AND mr.winning_outcome IS NOT NULL
        """
    ).fetchall()
    cols = ("market_id", "provider", "provider_market_id", "question", "category",
            "classified_category", "event_type", "resolved_at", "winning_outcome")
    return [dict(zip(cols, r, strict=True)) for r in rows]


def run_proof_of_edge_backtest(
    conn: sqlite3.Connection, markets: list[dict] | None = None
) -> list[ProofOfEdgeCase]:
    if markets is None:
        markets = load_eligible_markets(conn)

    cases: list[ProofOfEdgeCase] = []
    for m in markets:
        resolved_at = datetime.fromisoformat(m["resolved_at"])
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=UTC)

        if m["event_type"] in MACRO_QUANT_EVENT_TYPES:
            cases.append(ProofOfEdgeCase(
                market_id=m["market_id"], provider=m["provider"], provider_market_id=m["provider_market_id"],
                question=m["question"], category=m["category"], domain=m["classified_category"],
                event_type=m["event_type"], resolved_at=resolved_at, forecast_time=resolved_at,
                benchmark_price=float("nan"), outcome_yes=False, independent_probability=None,
                forecast_status="EXCLUDED", forecast_maturity=None,
                excluded_reason="macro_quant_no_pit_external_data",
            ))
            continue

        points = conn.execute(
            "SELECT captured_at, yes_price FROM polymarket_price_history WHERE market_id = ? ORDER BY captured_at",
            (m["market_id"],),
        ).fetchall()
        picked = _select_forecast_point(points, resolved_at)
        if picked is None:
            cases.append(ProofOfEdgeCase(
                market_id=m["market_id"], provider=m["provider"], provider_market_id=m["provider_market_id"],
                question=m["question"], category=m["category"], domain=m["classified_category"],
                event_type=m["event_type"], resolved_at=resolved_at, forecast_time=resolved_at,
                benchmark_price=float("nan"), outcome_yes=False, independent_probability=None,
                forecast_status="EXCLUDED", forecast_maturity=None,
                excluded_reason="no_price_point_before_resolution",
            ))
            continue

        forecast_time, benchmark_price = picked
        outcome_yes = str(m["winning_outcome"]).strip().lower() == "yes"

        prediction = compute_prediction(
            conn,
            market_id=m["market_id"],
            provider=m["provider"],
            provider_market_id=m["provider_market_id"],
            category=m["category"],
            classified_category=m["classified_category"],
            market_yes_price=benchmark_price,
            liquidity=0.0,
            data_quality_report_score=None,
            news_count=0,
            news_agreement=None,
            resolution_rules_present=False,
            question=m["question"] or "",
            resolution_text=None,
            as_of=forecast_time,
        )

        cases.append(ProofOfEdgeCase(
            market_id=m["market_id"], provider=m["provider"], provider_market_id=m["provider_market_id"],
            question=m["question"], category=m["category"], domain=m["classified_category"],
            event_type=m["event_type"], resolved_at=resolved_at, forecast_time=forecast_time,
            benchmark_price=benchmark_price, outcome_yes=outcome_yes,
            independent_probability=prediction.independent_probability,
            forecast_status=prediction.forecast_status,
            forecast_maturity=getattr(prediction, "forecast_maturity", None),
        ))
    return cases
