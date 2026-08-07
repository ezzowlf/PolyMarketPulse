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

from ..price_analytics import PricePoint
from .bayesian import bayesian_update
from .confidence import compute_confidence
from .cross_market import compute_cross_market_relations
from .deadline import classify_deadline_phase, deadline_weights_for
from .divergence import evaluate_divergence_safety
from .ensemble import combine_submodels
from .event_relations import collect_event_relation_signals, compute_event_relation_estimate
from .evidence import compute_independent_evidence
from .geopolitics import analyze_geopolitics
from .history import compute_history_estimate
from .macro import analyze_macro
from .manipulation import compute_manipulation_risk
from .market_flow import load_flow_metrics_from_db
from .momentum import compute_momentum_estimate
from .news import collect_news_evidence, compute_news_estimate
from .politics import analyze_politics
from .quant import analyze_quant
from .reaction_lag import STATUS_REACTED, compute_market_reaction_lag
from .reliability import compute_market_reliability
from .resolution_edge import compute_resolution_edge
from .scenarios import build_scenarios
from .semantics import parse_market_proposition
from .sports import analyze_sports
from .types import (
    ContributionEntry,
    DataQualityBreakdown,
    ForecastStatus,
    PredictionResult,
    Recommendation,
    SubmodelEstimate,
)

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
        weight=0.45 if independent_evidence.available else 0.0, available=independent_evidence.available,
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
    independent_evidence_estimate = SubmodelEstimate(
        name="independent_evidence",
        estimated_yes_probability=independent_evidence.independent_yes_probability,
        weight=(0.45 * deadline_weights.news_weight) if independent_evidence.available else 0.0,
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
    specialized_estimates: list[SubmodelEstimate] = []
    proposition = parse_market_proposition(question, resolution_text)
    reasoning.append(f"Specialized model event_type: {proposition.event_type or 'none'}")

    # Quant model (price-threshold markets)
    if proposition.event_type in ("price_above", "price_below") and proposition.asset:
        quant_result = analyze_quant(
            text=question,
            event_type=proposition.event_type,
            proposition_status=proposition.proposition_status,
            threshold=proposition.threshold,
            asset=proposition.asset,
            current_price=None,  # Placeholder — real price would come from price_analytics or external API
            historical_volatility=None,  # Placeholder — real volatility from historical price data
            deadline=proposition.deadline,
        )
        if quant_result.available and quant_result.probability is not None:
            quant_estimate = SubmodelEstimate(
                name="quant",
                estimated_yes_probability=quant_result.probability,
                weight=0.35,
                available=True,
                detail=f"Quant model: {quant_result.reason} (confidence: {quant_result.confidence}%)",
            )
            specialized_estimates.append(quant_estimate)
            reasoning.append(quant_result.reason)
        else:
            reasoning.append(f"Quant model unavailable: {quant_result.reason}")
            # Still add as unavailable for reporting
            specialized_estimates.append(SubmodelEstimate(
                name="quant",
                estimated_yes_probability=None,
                weight=0.0,
                available=False,
                detail=f"Quant model unavailable: {quant_result.reason}",
            ))

    # Politics model (office, resignation, legislation, etc.)
    if proposition.event_type in (
        "office_departure", "office_status", "resignation", "removal",
        "impeachment", "election", "legislation", "appointment", "court_outcome",
    ):
        politics_result = analyze_politics(
            text=question,
            event_type=proposition.event_type,
            proposition_status=proposition.proposition_status,
            subject=proposition.subject,
            location=proposition.location,
            historical_baseline=None,
        )
        if politics_result.available and politics_result.probability is not None:
            politics_estimate = SubmodelEstimate(
                name="politics",
                estimated_yes_probability=politics_result.probability,
                weight=0.35,
                available=True,
                detail=f"Politics model: {politics_result.reason} (confidence: {politics_result.confidence}%)",
            )
            specialized_estimates.append(politics_estimate)
            reasoning.append(politics_result.reason)
        else:
            reasoning.append(f"Politics model unavailable: {politics_result.reason}")
            specialized_estimates.append(SubmodelEstimate(
                name="politics",
                estimated_yes_probability=None,
                weight=0.0,
                available=False,
                detail=f"Politics model unavailable: {politics_result.reason}",
            ))

    # Geopolitics model (ceasefire, war_escalation, etc.)
    if proposition.event_type in (
        "ceasefire", "war_escalation", "military_action", "sanctions",
        "territorial_control", "strategic_waterway", "diplomatic_agreement",
    ):
        geopolitics_result = analyze_geopolitics(
            text=question,
            event_type=proposition.event_type,
            proposition_status=proposition.proposition_status,
            historical_baseline=None,
        )
        if geopolitics_result.available and geopolitics_result.probability is not None:
            geopolitics_estimate = SubmodelEstimate(
                name="geopolitics",
                estimated_yes_probability=geopolitics_result.probability,
                weight=0.35,
                available=True,
                detail=f"Geopolitics model: {geopolitics_result.reason} (confidence: {geopolitics_result.confidence}%)",
            )
            specialized_estimates.append(geopolitics_estimate)
            reasoning.append(geopolitics_result.reason)
        else:
            reasoning.append(f"Geopolitics model unavailable: {geopolitics_result.reason}")
            specialized_estimates.append(SubmodelEstimate(
                name="geopolitics",
                estimated_yes_probability=None,
                weight=0.0,
                available=False,
                detail=f"Geopolitics model unavailable: {geopolitics_result.reason}",
            ))

    # Macro model (central bank decisions, rate cuts/hikes/holds)
    if proposition.event_type in (
        "central_bank_decision", "rate_cut", "rate_hike", "rate_hold",
        "monetary_policy", "policy_change",
    ):
        macro_result = analyze_macro(
            text=question,
            event_type=proposition.event_type,
            proposition_status=proposition.proposition_status,
            historical_baseline=None,
        )
        if macro_result.available and macro_result.probability is not None:
            macro_estimate = SubmodelEstimate(
                name="macro",
                estimated_yes_probability=macro_result.probability,
                weight=0.35,
                available=True,
                detail=f"Macro model: {macro_result.reason} (confidence: {macro_result.confidence}%)",
            )
            specialized_estimates.append(macro_estimate)
            reasoning.append(macro_result.reason)
        else:
            reasoning.append(f"Macro model unavailable: {macro_result.reason}")
            specialized_estimates.append(SubmodelEstimate(
                name="macro",
                estimated_yes_probability=None,
                weight=0.0,
                available=False,
                detail=f"Macro model unavailable: {macro_result.reason}",
            ))

    # Sports model (POLYMARKET SPORTS only)
    if proposition.event_type in (
        "sport_match", "sport_tournament", "sport_qualification",
        "sport_winner", "sport_final",
    ):
        sports_result = analyze_sports(
            text=question,
            event_type=proposition.event_type,
            proposition_status=proposition.proposition_status,
            sport=None,
            team1=None,
            team2=None,
        )
        if sports_result.available and sports_result.probability is not None:
            sports_estimate = SubmodelEstimate(
                name="sports",
                estimated_yes_probability=sports_result.probability,
                weight=0.35,
                available=True,
                detail=f"Sports model: {sports_result.reason} (confidence: {sports_result.confidence}%)",
            )
            specialized_estimates.append(sports_estimate)
            reasoning.append(sports_result.reason)
        else:
            reasoning.append(f"Sports model unavailable: {sports_result.reason}")
            specialized_estimates.append(SubmodelEstimate(
                name="sports",
                estimated_yes_probability=None,
                weight=0.0,
                available=False,
                detail=f"Sports model unavailable: {sports_result.reason}",
            ))

    # Combine specialized estimates into independent_probability
    if specialized_estimates:
        specialized_available = [e for e in specialized_estimates if e.available]
        if specialized_available:
            specialized_independent_prob, _ = combine_submodels(specialized_available)
            # Update independent_probability to include specialized models
            # (combine with history + independent evidence)
            combined_independent, _ = combine_submodels(
                [history_estimate, independent_evidence_estimate] + specialized_available
            )
            independent_probability = combined_independent
            reasoning.append(f"Specialized models contributed to independent probability: {specialized_independent_prob:.1%}")

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
        )
        for s in all_submodels
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
