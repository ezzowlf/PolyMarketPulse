"""Prediction Engine V2 — orchestrator. Wires the independent submodels
(history, momentum, deadline, news+Bayesian) into one ensemble estimate,
computes confidence and data quality, builds scenarios, and returns the
single binding `PredictionResult` that the GPT-5 nano explanation layer is
only ever allowed to explain (never invent or override — see
ai/validation.py).

Kept signature-compatible with the V1 `compute_prediction()` so existing
callers (ai/service.py, scripts/generate_acceptance_examples.py) do not
need to change; the richer V2 inputs (resolution date, price-snapshot
history, linked news) are queried internally from `market_id` /
`provider` / `provider_market_id`, which every caller already has.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ..price_analytics import PricePoint
from .bayesian import bayesian_update
from .confidence import compute_confidence
from .deadline import classify_deadline_phase, deadline_weights_for
from .ensemble import combine_submodels
from .evidence import compute_independent_evidence
from .history import compute_history_estimate
from .momentum import compute_momentum_estimate
from .news import collect_news_evidence, compute_news_estimate
from .scenarios import build_scenarios
from .types import DataQualityBreakdown, PredictionResult, Recommendation, SubmodelEstimate

PREDICTION_VERSION = "v2"

EDGE_NO_BET = 0.03
EDGE_WATCH = 0.08
EDGE_STRONG = 0.18
MIN_CONFIDENCE_FOR_ACTION = 40
MIN_COMPARABLE_SAMPLE = 5  # kept for backward-compat imports (tests/test_prediction.py)


def _recommendation(net_edge: float | None, confidence: float, sample_size: int) -> Recommendation:
    """Preserved from V1 — the empirically documented, un-tuned threshold
    logic backtest.py and existing tests already rely on."""
    if net_edge is None or sample_size < MIN_COMPARABLE_SAMPLE:
        return "INSUFFICIENT_DATA"
    if confidence < MIN_CONFIDENCE_FOR_ACTION:
        return "NO_BET"
    magnitude = abs(net_edge)
    if magnitude < EDGE_NO_BET:
        return "NO_BET"
    is_yes = net_edge > 0
    if magnitude >= EDGE_STRONG:
        return "STRONG_YES" if is_yes else "STRONG_NO"
    if magnitude >= EDGE_WATCH:
        return "YES" if is_yes else "NO"
    return "WATCH_YES" if is_yes else "WATCH_NO"


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone() is not None


def _load_resolution_date(conn: sqlite3.Connection, market_id: str) -> datetime | None:
    # Tolerates minimal/partial schemas (e.g. isolated unit tests that only
    # create the columns their scenario needs) — a missing table/column
    # simply means "resolution date unknown", not a crash.
    if not _table_exists(conn, "markets") or not _table_has_column(conn, "markets", "end_date"):
        return None
    row = conn.execute("SELECT end_date FROM markets WHERE market_id = ?", (market_id,)).fetchone()
    if row is None or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def _load_price_points(conn: sqlite3.Connection, market_id: str, limit: int = 60) -> list[PricePoint]:
    if not _table_exists(conn, "market_snapshots"):
        return []
    rows = conn.execute(
        "SELECT captured_at, yes_price, liquidity, volume_24h, spread FROM market_snapshots "
        "WHERE market_id = ? ORDER BY captured_at DESC LIMIT ?",
        (market_id, limit),
    ).fetchall()
    points = [
        PricePoint(captured_at=r[0], yes_price=r[1], liquidity=r[2], volume_24h=r[3], spread=r[4])
        for r in rows
    ]
    points.reverse()  # chronological order, as price_analytics expects
    return points


def compute_prediction(
    conn: sqlite3.Connection,
    market_id: str,
    provider: str,
    provider_market_id: str,
    category: str | None,
    market_yes_price: float | None,
    liquidity: float,
    data_quality_report_score: float | None,
    news_count: int,
    news_agreement: float | None,
    resolution_rules_present: bool,
    question: str = "",
    resolution_text: str | None = None,
) -> PredictionResult:
    reasoning: list[str] = []
    now = datetime.now(UTC)

    # --- Deadline Engine -------------------------------------------------
    resolution_date = _load_resolution_date(conn, market_id)
    deadline_phase = classify_deadline_phase(now, resolution_date)
    deadline_weights = deadline_weights_for(deadline_phase)
    reasoning.append(f"Deadline-Phase: {deadline_phase} (News-Gewicht {deadline_weights.news_weight:.2f}).")

    # --- History submodel --------------------------------------------------
    history_estimate, comparable_sample_size, observed_yes_rate = compute_history_estimate(
        conn, category, provider
    )
    reasoning.append(history_estimate.detail)

    # --- Momentum submodel ---------------------------------------------
    price_points = _load_price_points(conn, market_id)
    momentum_value, price_analytics, momentum_detail = compute_momentum_estimate(price_points, market_yes_price)
    momentum_estimate = SubmodelEstimate(
        name="momentum", estimated_yes_probability=momentum_value,
        weight=(0.4 * deadline_weights.momentum_weight) if momentum_value is not None else 0.0,
        available=momentum_value is not None, detail=momentum_detail,
    )
    reasoning.append(momentum_detail)

    # Apply the deadline's history-weight multiplier now that both base
    # weights exist, so the closing-minutes phase correctly de-emphasizes
    # the slow-moving historical base rate relative to momentum/news.
    history_estimate = SubmodelEstimate(
        name=history_estimate.name, estimated_yes_probability=history_estimate.estimated_yes_probability,
        weight=history_estimate.weight * deadline_weights.history_weight,
        available=history_estimate.available, detail=history_estimate.detail,
    )

    # --- Independent Evidence & Early-Signal Engine -----------------------
    # Computed WITHOUT market_yes_price as an anchor (see evidence.py) —
    # only afterward compared against it to report divergence/edge. Feeds
    # into the ensemble like any other submodel so real, independent
    # evidence can actually move the recommendation (not just be displayed).
    independent_evidence = compute_independent_evidence(
        conn, provider=provider, provider_market_id=provider_market_id,
        question=question, resolution_text=resolution_text,
        market_yes_price=market_yes_price, now=now,
    )
    independent_evidence_estimate = SubmodelEstimate(
        name="independent_evidence",
        estimated_yes_probability=independent_evidence.independent_yes_probability,
        weight=(0.45 * deadline_weights.news_weight) if independent_evidence.available else 0.0,
        available=independent_evidence.available,
        detail=independent_evidence.detail,
    )
    reasoning.append(independent_evidence.detail)

    # --- Ensemble: history + momentum + independent evidence -> prior ----
    prior_estimate, _ = combine_submodels([history_estimate, momentum_estimate, independent_evidence_estimate])
    if prior_estimate is None:
        prior_estimate = market_yes_price  # last resort: no submodel had enough to say anything

    # --- News submodel + Bayesian update ---------------------------------
    news_evidence = collect_news_evidence(conn, provider, provider_market_id, now=now)
    news_estimate, weighted_sentiment, confirmation_count = compute_news_estimate(news_evidence, market_yes_price)
    reasoning.append(news_estimate.detail)

    if prior_estimate is not None:
        bayes = bayesian_update(
            prior_probability=prior_estimate, weighted_news_sentiment=weighted_sentiment,
            confirmation_count=confirmation_count, news_weight_multiplier=deadline_weights.news_weight,
        )
        estimated_yes = bayes.posterior_probability
        reasoning.append(bayes.detail)
    else:
        estimated_yes = None

    estimated_no = (1 - estimated_yes) if estimated_yes is not None else None

    market_yes = market_yes_price
    market_no = (1 - market_yes) if market_yes is not None else None

    gross_edge = None
    net_edge = None
    if estimated_yes is not None and market_yes is not None:
        gross_edge = round(estimated_yes - market_yes, 4)
        cost_haircut = 0.02
        net_edge = gross_edge - cost_haircut if gross_edge > 0 else gross_edge + cost_haircut
        if abs(net_edge) < 1e-9:
            net_edge = 0.0
        reasoning.append(f"Netto-Edge nach pauschalem Kosten-/Spread-Abschlag von {cost_haircut:.0%}: {net_edge:+.1%}.")

    # --- Data quality (unchanged shape from V1) ---------------------------
    dq = DataQualityBreakdown(
        vollstaendigkeit=90.0 if data_quality_report_score and data_quality_report_score >= 90 else 60.0,
        aktualitaet=85.0,
        quellenuebereinstimmung=round(min(100.0, (news_agreement or 0.5) * 100), 1) if news_count else 50.0,
        historische_fallzahl=round(min(100.0, comparable_sample_size * 8.0), 1),
        resolution_klarheit=90.0 if resolution_rules_present else 40.0,
        liquiditaet=round(min(100.0, (liquidity / 100_000) * 40), 1),
    )

    # --- Confidence (new: ensemble-aware, separate from probability) -----
    all_submodels = [history_estimate, momentum_estimate, news_estimate, independent_evidence_estimate]
    market_stability = 1.0
    if price_analytics is not None and price_analytics.volatility is not None:
        market_stability = max(0.0, 1 - min(1.0, price_analytics.volatility * 10))
    confidence, ensemble_agreement = compute_confidence(
        dq, all_submodels, market_stability=market_stability, deadline_phase_known=resolution_date is not None
    )

    uncertainty_lower = uncertainty_upper = None
    if estimated_yes is not None:
        spread = max(0.05, 0.25 - (confidence / 100) * 0.2)
        uncertainty_lower = round(max(0.0, estimated_yes - spread), 4)
        uncertainty_upper = round(min(1.0, estimated_yes + spread), 4)

    recommendation = _recommendation(net_edge, confidence, comparable_sample_size)

    scenarios = build_scenarios(
        estimated_yes_probability=estimated_yes, submodel_estimates=all_submodels,
        news_evidence=news_evidence, comparable_sample_size=comparable_sample_size,
        recommendation=recommendation,
    )

    return PredictionResult(
        market_id=market_id,
        market_yes_probability=market_yes,
        market_no_probability=market_no,
        estimated_yes_probability=estimated_yes,
        estimated_no_probability=estimated_no,
        gross_yes_edge=gross_edge,
        net_yes_edge=net_edge,
        confidence_score=confidence,
        data_quality=dq,
        uncertainty_lower=uncertainty_lower,
        uncertainty_upper=uncertainty_upper,
        recommendation=recommendation,
        comparable_sample_size=comparable_sample_size,
        observed_historical_yes_rate=observed_yes_rate,
        reasoning_notes=tuple(reasoning),
        deadline_phase=deadline_phase,
        submodel_estimates=tuple(all_submodels),
        ensemble_agreement=ensemble_agreement,
        scenarios=scenarios,
        news_sentiment_score=weighted_sentiment,
        news_confirmation_count=confirmation_count,
        independent_evidence=independent_evidence,
    )
