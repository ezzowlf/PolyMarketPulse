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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..price_analytics import PricePoint
from ..providers.coingecko import fetch_price_and_volatility, resolve_coingecko_id
from .bayesian import bayesian_update
from .confidence import compute_confidence
from .cross_market import compute_cross_market_relations
from .deadline import classify_deadline_phase, deadline_weights_for
from .divergence import evaluate_divergence_safety
from .ensemble import combine_submodels, quality_scaled_weight
from .event_relations import collect_event_relation_signals, compute_event_relation_estimate
from .evidence import compute_independent_evidence
from .history import compute_history_estimate
from .manipulation import compute_manipulation_risk
from .market_flow import load_flow_metrics_from_db
from .momentum import compute_momentum_estimate
from .news import collect_news_evidence, compute_news_estimate
from .reaction_lag import STATUS_REACTED, compute_market_reaction_lag
from .reliability import compute_market_reliability
from .resolution_edge import compute_resolution_edge
from .scenarios import build_scenarios
from .semantics import parse_market_proposition
from .specialized_router import ALL_SPECIALIZED_MODEL_NAMES, route_to_specialized_model
from .types import (
    ContributionEntry,
    DataQualityBreakdown,
    ForecastStatus,
    PredictionResult,
    Recommendation,
    SubmodelEstimate,
)

if TYPE_CHECKING:
    from .evidence import IndependentEvidenceResult

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


def _evidence_quality(independent_evidence: IndependentEvidenceResult) -> float:
    """Real quality signal (0..1) for the independent_evidence submodel's
    ensemble weight, per Phase F. Combines two numbers evidence.py already
    computes honestly from the linked evidence itself (no invented number):

      - source_quality_score (0..100): the average of
        reliability * recency_weight * link_confidence * relation_weight
        across every scored evidence item — i.e. how trustworthy, fresh,
        topically-relevant, and strongly-entailing the evidence actually
        is.
      - confirmation_count: how many independently-confirming domains
        support the same direction, capped at 3 for this factor so a
        4th+ confirming domain doesn't keep buying more weight forever.

    quality = source_quality_fraction * (0.5 + 0.5 * confirmation_factor)
    — a single high-quality domain still gets meaningful (half-strength)
    weight; multiple confirming domains double that up to full strength.
    Returns 0.0 when the submodel is unavailable (no evidence at all)."""
    if not independent_evidence.available:
        return 0.0
    source_quality_frac = (independent_evidence.source_quality_score or 0.0) / 100.0
    confirmation_factor = min(1.0, independent_evidence.confirmation_count / 3.0)
    return source_quality_frac * (0.5 + 0.5 * confirmation_factor)


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


def _forecast_status(
    estimated_yes: float | None, independent_probability: float | None,
    submodel_estimates: list[SubmodelEstimate], confidence: float,
) -> ForecastStatus:
    """Six distinguishable states, per the product requirement that the UI
    never show a bare probability without saying what kind of forecast it
    is:
      NO_FORECAST         nothing at all contributed
      BASELINE_ONLY        only the historical baseline (no news/evidence)
      EVIDENCE_ONLY         only independent evidence (no historical baseline)
      INDEPENDENT_FORECAST  a real market-blind combination of the two
      BLENDED_FORECAST      the above, further mixed with market-price-
                             anchored submodels (momentum/news/event-relations)
      LOW_DATA              something combined, but confidence is too low to trust it
    """
    if estimated_yes is None:
        return "NO_FORECAST"
    available_names = {s.name for s in submodel_estimates if s.available}
    independent_names = available_names & {"history", "independent_evidence"}
    price_anchored_names = available_names & {"momentum", "news", "event_relations"}

    if independent_probability is None or not independent_names:
        return "BLENDED_FORECAST" if price_anchored_names else "NO_FORECAST"

    if independent_names == {"history"} and not price_anchored_names:
        return "BASELINE_ONLY"
    if independent_names == {"independent_evidence"} and not price_anchored_names:
        return "EVIDENCE_ONLY"

    base_status: ForecastStatus = "BLENDED_FORECAST" if price_anchored_names else "INDEPENDENT_FORECAST"
    if confidence < 45.0:
        return "LOW_DATA"
    return base_status


def market_blind_forecast(
    conn: sqlite3.Connection,
    provider: str,
    provider_market_id: str,
    category: str | None,
    question: str = "",
    resolution_text: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Diagnostic entrypoint (spec requirement: 'market_blind_forecast').
    Computes an independent probability WITHOUT the current market price
    ever being passed in as an argument to anything in this call chain —
    not "ignored after being read", genuinely never received. Only the two
    submodels that don't take a market price parameter at all (history,
    independent evidence) are used; `market_yes_price` is hardcoded to
    `None` in both calls below, so there is nothing for the market price to
    leak in through.

    Returns a plain dict (not PredictionResult) since this is a standalone
    diagnostic/audit tool, not part of the production forecast path."""
    now = now or datetime.now(UTC)
    history_estimate, comparable_sample_size, observed_yes_rate = compute_history_estimate(
        conn, category, provider, question=question, resolution_text=resolution_text,
    )
    independent_evidence = compute_independent_evidence(
        conn, provider=provider, provider_market_id=provider_market_id,
        question=question, resolution_text=resolution_text,
        market_yes_price=None, now=now,  # <- hardcoded None: the market price cannot reach this call
    )
    independent_evidence_estimate = SubmodelEstimate(
        name="independent_evidence", estimated_yes_probability=independent_evidence.independent_yes_probability,
        weight=quality_scaled_weight(0.45, _evidence_quality(independent_evidence)),
        available=independent_evidence.available,
        detail=independent_evidence.detail,
    )
    blind_probability, _ = combine_submodels([history_estimate, independent_evidence_estimate])
    return {
        "blind_independent_probability": blind_probability,
        "comparable_sample_size": comparable_sample_size,
        "observed_historical_yes_rate": observed_yes_rate,
        "independent_evidence_available": independent_evidence.available,
        "history_available": history_estimate.available,
        "detail": (
            f"history={'verfügbar' if history_estimate.available else 'nicht verfügbar'}, "
            f"independent_evidence={'verfügbar' if independent_evidence.available else 'nicht verfügbar'}"
        ),
    }


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
        conn, category, provider, question=question, resolution_text=resolution_text,
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
    # Weight is quality-scaled (Phase F), not a flat "available ? 0.45 : 0"
    # constant — see `_evidence_quality` for the exact formula and which
    # real signals (source_quality_score, confirmation_count) drive it.
    independent_evidence_estimate = SubmodelEstimate(
        name="independent_evidence",
        estimated_yes_probability=independent_evidence.independent_yes_probability,
        weight=quality_scaled_weight(0.45 * deadline_weights.news_weight, _evidence_quality(independent_evidence)),
        available=independent_evidence.available,
        detail=independent_evidence.detail,
    )
    reasoning.append(independent_evidence.detail)

    # --- Structural edge analysis (additive, doesn't feed the ensemble) --
    resolution_edge = compute_resolution_edge(question, resolution_text, authority_source=resolution_text)
    cross_market = compute_cross_market_relations(conn, market_id, provider, question, market_yes_price)
    first_evidence_at = None
    if independent_evidence.time_since_first_report_hours is not None:
        first_evidence_at = now - timedelta(hours=independent_evidence.time_since_first_report_hours)
    reaction_lag = compute_market_reaction_lag(conn, market_id, first_evidence_at, now=now)

    # --- Public market-flow / order-book / wallet intelligence -----------
    # Reads collector output (see cli.py `flow-fetch`); computes no network
    # calls itself. A market price move without any linked evidence for it
    # is the same "breaking but not explained" signal reaction_lag/evidence
    # already track — reused here as a reliability/manipulation input.
    orderbook_metrics, trade_flow_metrics, wallet_concentration = load_flow_metrics_from_db(
        conn, provider, provider_market_id
    )
    price_moved_without_evidence = (
        reaction_lag.status == STATUS_REACTED and not independent_evidence.available
    )
    market_reliability = compute_market_reliability(
        resolution_edge_score=resolution_edge.resolution_edge_score,
        orderbook_imbalance=orderbook_metrics.imbalance if orderbook_metrics.available else None,
        orderbook_thin=orderbook_metrics.thin if orderbook_metrics.available else None,
        wallet_concentration_score=wallet_concentration.concentration_score if wallet_concentration.available else None,
        cross_market_inconsistency_score=cross_market.logical_inconsistency_score if cross_market.available else None,
        price_moved_without_evidence=price_moved_without_evidence,
    )
    manipulation_risk = compute_manipulation_risk(
        orderbook_thin=orderbook_metrics.thin if orderbook_metrics.available else None,
        large_trade_ratio=trade_flow_metrics.large_trade_ratio if trade_flow_metrics.available else None,
        price_moved_without_evidence=price_moved_without_evidence,
        wallet_concentration_score=wallet_concentration.concentration_score if wallet_concentration.available else None,
        deadline_hours=(resolution_date - now).total_seconds() / 3600 if resolution_date else None,
    )

    # --- Event-Relations (causal-reasoning foundation) --------------------
    # Only KNOWN/STRONG_EVIDENCE/SUPPORTED relations ever get nonzero
    # weight here (see events.py); PLAUSIBLE/SPECULATIVE ones are still
    # returned on the result for explainability but never move the number.
    event_relation_signals = collect_event_relation_signals(conn, provider, provider_market_id)
    event_relation_estimate = compute_event_relation_estimate(event_relation_signals, market_yes_price)
    reasoning.append(event_relation_estimate.detail)

    # --- Independent probability (market-price-blind by construction) ----
    # Reuses the already-computed history_estimate/independent_evidence_estimate
    # objects — neither of those two submodels ever received market_yes_price
    # as an input to their own probability computation (see history.py's
    # signature and evidence.py's 0.5 prior), so this combine is genuinely
    # market-blind, not just "close to it". This is the number the product
    # needs to answer "what does the system believe without looking at the
    # market at all?" — see market_blind_forecast() for the fully standalone
    # version of the same computation, used for auditing this claim.
    independent_probability, _ = combine_submodels([history_estimate, independent_evidence_estimate])

    # --- Specialized models (contribute ONLY to independent_probability) --
    # These models never receive market_yes_price as an input — they are
    # genuinely market-blind, computing probability from structured data
    # or historical baselines only. They feed into independent_probability
    # via combine_submodels but are NEVER used for momentum/news/Bayesian
    # updates that would tether them to the market price.
    # Routed through specialized_router.route_to_specialized_model() — the
    # router is the single source of truth for event_type -> model
    # eligibility (see specialized_router._EVENT_TYPE_TO_MODEL), replacing
    # what used to be five duplicated inline event_type checks here. Only
    # the underlying CoinGecko price/volatility lookup (needed as an input
    # *to* the quant model, not part of routing itself) stays in engine.py,
    # and only runs when the proposition could plausibly be a quant one —
    # no point spending an HTTP call on a market that isn't price-threshold
    # shaped at all.
    proposition = parse_market_proposition(question, resolution_text)
    reasoning.append(f"Specialized model event_type: {proposition.event_type or 'none'}")

    quant_current_price = None
    quant_daily_volatility = None
    if proposition.event_type in ("price_above", "price_below") and proposition.asset:
        coingecko_id = resolve_coingecko_id(proposition.asset)
        if coingecko_id:
            price_data = fetch_price_and_volatility(coingecko_id)
            if price_data is not None:
                quant_current_price = price_data.current_price
                quant_daily_volatility = price_data.daily_volatility

    routing = route_to_specialized_model(
        proposition, question,
        current_price=quant_current_price,  # real price from CoinGecko free tier, or None if unavailable
        historical_volatility=quant_daily_volatility,  # real realized daily vol from CoinGecko history, or None
    )
    reasoning.extend(routing.reasons)

    # specialized_eligibility feeds contribution_breakdown's `eligible`
    # field (Phase F / ContributionEntry) so "not a candidate for this
    # market's event_type" (eligible=False) is visibly distinct from
    # "was a candidate but had no usable data" (eligible=True,
    # available=False) — see ALL_SPECIALIZED_MODEL_NAMES.
    specialized_eligibility: dict[str, bool] = {
        name: name in routing.eligible_models for name in ALL_SPECIALIZED_MODEL_NAMES
    }

    specialized_estimates: list[SubmodelEstimate] = []
    if routing.used_models:
        # The router only ever selects (and runs) one primary model per
        # market (routing.used_models has at most one entry today) — see
        # route_to_specialized_model's `selected_model = eligible_models[0]`.
        selected_name = routing.used_models[0]
        result_dict = routing.model_results[0]
        model_confidence = float(result_dict.get("confidence") or 0.0)
        # Weight is quality-scaled by the model's OWN confidence output
        # (real: each model derives it from z-score magnitude / data
        # completeness / event-strength heuristics — see that model's
        # module), never a flat constant. Base ceiling of 0.45 matches
        # independent_evidence's own base — specialized models are not
        # given a structural advantage over independently-sourced evidence,
        # only whatever their own confidence earns them.
        weight = quality_scaled_weight(0.45, model_confidence / 100.0)
        specialized_estimates.append(
            SubmodelEstimate(
                name=selected_name,
                estimated_yes_probability=result_dict.get("probability"),
                weight=weight,
                available=True,
                detail=f"{selected_name.capitalize()} model: {result_dict.get('reason')} (confidence: {model_confidence:.0f}%)",
            )
        )
    for unavailable_name in routing.unavailable_models:
        specialized_estimates.append(
            SubmodelEstimate(
                name=unavailable_name, estimated_yes_probability=None, weight=0.0, available=False,
                detail=f"{unavailable_name.capitalize()} model eligible for this market but unavailable "
                       f"(see routing reasons above).",
            )
        )

    # Combine specialized estimates into independent_probability
    specialized_available = [e for e in specialized_estimates if e.available]
    if specialized_available:
        # Update independent_probability to include the specialized model
        # (combine with history + independent evidence)
        combined_independent, _ = combine_submodels(
            [history_estimate, independent_evidence_estimate] + specialized_available
        )
        independent_probability = combined_independent
        reasoning.append(
            f"Specialized model '{specialized_available[0].name}' contributed to independent probability "
            f"(weight={specialized_available[0].weight:.3f})."
        )

    # --- Ensemble: history + momentum + independent evidence -> prior ----
    # No market-price fallback here: if none of the independent submodels
    # produced an estimate, `prior_estimate` stays None, which flows through
    # to `estimated_yes = None` below and `_recommendation()` correctly
    # reports INSUFFICIENT_DATA. Silently defaulting the prior to the
    # market's own price would make the engine echo the market whenever it
    # actually has nothing independent to say — exactly the bug this
    # comment now prevents from being reintroduced.
    prior_estimate, _ = combine_submodels(
        [history_estimate, momentum_estimate, independent_evidence_estimate, event_relation_estimate]
    )

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
        # Shrinks the edge's *magnitude* toward zero by the assumed cost/
        # spread drag — it must never manufacture an edge that isn't there.
        # A zero gross edge always yields a zero net edge; a small edge
        # smaller than the haircut correctly rounds down to zero rather
        # than flipping sign or getting pushed further from zero.
        if gross_edge > 0:
            net_edge = round(max(0.0, gross_edge - cost_haircut), 4)
        elif gross_edge < 0:
            net_edge = round(min(0.0, gross_edge + cost_haircut), 4)
        else:
            net_edge = 0.0
        reasoning.append(f"Netto-Edge nach pauschalem Kosten-/Spread-Abschlag von {cost_haircut:.0%}: {net_edge:+.1%}.")

    # --- Data quality (unchanged shape from V1) ---------------------------
    dq = DataQualityBreakdown(
        vollstaendigkeit=90.0 if data_quality_report_score and data_quality_report_score >= 90 else 60.0,
        # KNOWN LIMITATION: not yet computed from the actual last-scan
        # timestamp — compute_prediction() has no snapshot-age input to
        # work with today. A fixed 85 means "Aktualität" cannot currently
        # drag an otherwise-poor market's data quality down. Wiring a real
        # snapshot-age input through is out of scope for this fix; see the
        # audit report's "offene Einschränkungen" section.
        aktualitaet=85.0,
        quellenuebereinstimmung=round(min(100.0, (news_agreement or 0.5) * 100), 1) if news_count else 50.0,
        historische_fallzahl=round(min(100.0, comparable_sample_size * 8.0), 1),
        resolution_klarheit=90.0 if resolution_rules_present else 40.0,
        liquiditaet=round(min(100.0, (liquidity / 100_000) * 40), 1),
    )

    # --- Confidence (new: ensemble-aware, separate from probability) -----
    all_submodels = [
        history_estimate, momentum_estimate, news_estimate, independent_evidence_estimate, event_relation_estimate,
    ] + specialized_estimates
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

    # --- Blended vs. calibrated -------------------------------------------
    # blended_probability is the full-ensemble number (identical value to
    # estimated_yes_probability — kept as a separate, explicitly named
    # field so callers never have to guess which of the four numbers
    # "estimated_yes_probability" actually is).
    #
    # calibrated_probability: a real, computed shrinkage toward the
    # uninformative 0.5 prior, scaled by how little we trust the estimate
    # (1 - confidence/100). This is deliberately simple (linear shrinkage,
    # not a fitted calibration curve from historical Brier/reliability data
    # — that requires enough resolved-market history to fit against, which
    # doesn't exist yet) but it is a real transformation of the blended
    # number, not a pass-through pretending to be calibration.
    blended_probability = estimated_yes
    calibrated_probability = None
    if blended_probability is not None:
        trust = max(0.3, min(1.0, confidence / 100))
        calibrated_probability = round(0.5 + (blended_probability - 0.5) * trust, 4)

    forecast_status = _forecast_status(estimated_yes, independent_probability, all_submodels, confidence)

    # --- Divergence safety (Phase B4) -------------------------------------
    # A large gap between the market-blind independent estimate and the
    # market's own price is only trustworthy if it's backed by real
    # evidence. "Strong evidence" here means either: a reasonably sized
    # historical comparable sample (10+, i.e. at least the LIMITED
    # confidence tier — see history.py), or independent evidence with at
    # least 2 independently-confirming sources AND at least one
    # DIRECT_YES/DIRECT_NO-tier (primary-source-strength) item. Anything
    # weaker than that, combined with a >15pp gap, gets suppressed rather
    # than reported as a fabricated-looking number.
    evidence_is_strong = bool(
        (history_estimate.available and comparable_sample_size >= 10)
        or (
            independent_evidence.available
            and independent_evidence.confirmation_count >= 2
            and any(
                f.relation_label in ("DIRECT_YES", "DIRECT_NO")
                for f in (*independent_evidence.evidence_for_yes, *independent_evidence.evidence_for_no)
            )
        )
    )
    divergence_safety = evaluate_divergence_safety(independent_probability, market_yes, evidence_is_strong)
    forecast_suppression_reason: str | None = None
    if divergence_safety.suppressed:
        reasoning.append(divergence_safety.reason)
        forecast_suppression_reason = divergence_safety.reason
        independent_probability = None
        forecast_status = "FORECAST_SUPPRESSED"

    total_available_weight = sum(s.weight for s in all_submodels if s.available)
    contribution_breakdown = tuple(
        ContributionEntry(
            source=s.name, available=s.available, estimated_yes_probability=s.estimated_yes_probability,
            weight_share=(round(s.weight / total_available_weight, 4) if s.available and total_available_weight > 0 else None),
            detail=s.detail,
            eligible=specialized_eligibility.get(s.name),
        )
        for s in all_submodels
    )
    # Specialized models that were never eligible for this market's
    # event_type/category (routing.eligible_models didn't include them) got
    # no SubmodelEstimate at all above — surfaced here as their own
    # ContributionEntry rows (eligible=False) so contribution_breakdown
    # genuinely enumerates "which of the 5 specialized models could this
    # market have used" and not just the ones that happened to run.
    _named_in_breakdown = {s.name for s in all_submodels}
    contribution_breakdown = contribution_breakdown + tuple(
        ContributionEntry(
            source=name, available=False, estimated_yes_probability=None, weight_share=None,
            detail=f"{name.capitalize()} model not eligible for this market's event_type "
                   f"('{proposition.event_type or 'none'}').",
            eligible=False,
        )
        for name in ALL_SPECIALIZED_MODEL_NAMES
        if name not in _named_in_breakdown
    )

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
        resolution_edge=resolution_edge,
        cross_market=cross_market,
        reaction_lag=reaction_lag,
        orderbook_metrics=orderbook_metrics,
        trade_flow_metrics=trade_flow_metrics,
        wallet_concentration=wallet_concentration,
        market_reliability=market_reliability,
        manipulation_risk=manipulation_risk,
        event_relation_signals=tuple(event_relation_signals),
        independent_probability=independent_probability,
        market_consensus_probability=market_yes,
        blended_probability=blended_probability,
        calibrated_probability=calibrated_probability,
        forecast_status=forecast_status,
        contribution_breakdown=contribution_breakdown,
        forecast_suppression_reason=forecast_suppression_reason,
    )
