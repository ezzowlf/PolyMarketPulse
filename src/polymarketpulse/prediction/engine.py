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
from dataclasses import replace as _dataclass_replace
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING

from ..data_gaps import calculate_data_gaps
from ..data_sources import row_to_provider_health
from ..price_analytics import PricePoint
from ..providers.coingecko import PriceData, fetch_price_and_volatility, resolve_coingecko_id
from ..providers.fred import MacroSnapshot, fetch_macro_snapshot
from .bayesian import bayesian_update
from .change_triggers import compute_change_triggers
from .conditional_transitions import derive_conditional_transitions
from .confidence import (
    compute_confidence,
    compute_confidence_composite,
    compute_data_quality_composite,
    compute_freshness_score,
)
from .cross_market import compute_cross_market_relations
from .deadline import classify_deadline_phase, deadline_weights_for
from .decision import compute_decision_state
from .divergence import DIVERGENCE_THRESHOLD_PP
from .divergence_audit import DivergenceAuditContext, audit_divergence, classify_divergence_support
from .ensemble import combine_submodels, quality_scaled_weight
from .event_clock import derive_event_clock
from .event_relations import collect_event_relation_signals, compute_event_relation_estimate
from .evidence import compute_independent_evidence
from .expected_vs_observed import derive_expected_vs_observed
from .history import compute_history_estimate
from .manipulation import compute_manipulation_risk
from .market_flow import load_flow_metrics_from_db
from .maturity import build_maturity_breakdown, classify_forecast_maturity
from .momentum import compute_momentum_estimate
from .news import collect_news_evidence, compute_news_estimate
from .next_event import derive_next_event
from .reaction_lag import STATUS_REACTED, compute_market_reaction_lag
from .reliability import compute_market_reliability
from .resolution_edge import compute_resolution_edge
from .resolution_semantics import extract_resolution_semantics
from .scenario_tree import derive_scenario_tree
from .scenarios import build_scenarios
from .semantics import MarketProposition, parse_market_proposition
from .sensitivity import derive_sensitivity_audit
from .specialized_router import ALL_SPECIALIZED_MODEL_NAMES, route_to_specialized_model
from .structured_state import assemble_structured_world_state
from .types import (
    ContributionEntry,
    DataQualityBreakdown,
    ForecastStatus,
    PredictionResult,
    Recommendation,
    SubmodelEstimate,
)
from .world_state import assemble_world_state

if TYPE_CHECKING:
    from .evidence import IndependentEvidenceResult

PREDICTION_VERSION = "v2"

EDGE_NO_BET = 0.03
EDGE_WATCH = 0.08
EDGE_STRONG = 0.18
MIN_CONFIDENCE_FOR_ACTION = 40
MIN_COMPARABLE_SAMPLE = 5  # kept for backward-compat imports (tests/test_prediction.py)

_PROVIDER_CACHE_TTL_SECONDS = 300.0
_provider_cache_lock = Lock()
_provider_cache: dict[tuple, tuple[float, object]] = {}


# Block D Part 1: influence-ranking labels.
#
# A real, computed field — never an arbitrary label — derived from two
# already-real signals: (1) how far this submodel's own
# estimated_yes_probability sits from the neutral 0.5 midpoint (the
# strength/direction of its opinion) and (2) its actual weight_share in the
# ensemble (how much that opinion actually mattered to the blend). This is
# deliberately the SAME pair of numbers `contribution_pp` uses
# ((p - 0.5) * weight_share) — influence_rank is just a coarse, always-
# available classification of that same real magnitude, so it stays honest
# even for submodels (news) where a precise pp figure cannot be derived
# because the submodel doesn't participate in ensemble.combine_submodels'
# weighted average at all (see types.ContributionEntry.influence_rank).
_INFLUENCE_STRONG_MAGNITUDE = 0.15  # |p-0.5| * weight_share threshold for STRONG_*
_INFLUENCE_MEDIUM_MAGNITUDE = 0.05  # ... for MEDIUM_*
_INFLUENCE_NEUTRAL_BAND = 0.02  # |p-0.5| itself below this -> NEUTRAL regardless of weight


def _classify_influence_rank(estimated_yes_probability: float | None, weight_share: float | None) -> str | None:
    """None when the submodel is unavailable/has no probability at all —
    "no opinion" is not the same as "neutral opinion". Otherwise always
    produces one of the five real labels from the actual (probability,
    weight_share) pair, never a guess."""
    if estimated_yes_probability is None:
        return None
    share = weight_share if weight_share is not None else 0.0
    delta = estimated_yes_probability - 0.5
    if abs(delta) < _INFLUENCE_NEUTRAL_BAND:
        return "NEUTRAL"
    magnitude = abs(delta) * share
    if magnitude >= _INFLUENCE_STRONG_MAGNITUDE:
        return "STRONG_POSITIVE" if delta > 0 else "STRONG_NEGATIVE"
    if magnitude >= _INFLUENCE_MEDIUM_MAGNITUDE:
        return "MEDIUM_POSITIVE" if delta > 0 else "MEDIUM_NEGATIVE"
    return "NEUTRAL"


def _cached_provider_call(key: tuple, fetch):
    """Small in-process TTL cache for repeated identical forecast inputs.

    The callable identity is part of each caller's key so monkeypatched test
    providers cannot leak cached values into another test. Failures (`None`)
    are cached too: an offline provider should not block every market-detail
    request until the short TTL expires.
    """
    now = monotonic()
    with _provider_cache_lock:
        cached = _provider_cache.get(key)
        if cached is not None and now - cached[0] < _PROVIDER_CACHE_TTL_SECONDS:
            return cached[1]
        value = fetch()
        _provider_cache[key] = (monotonic(), value)
        return value


def _fetch_quant_snapshot(coingecko_id: str) -> PriceData | None:
    key = ("coingecko", id(fetch_price_and_volatility), coingecko_id, 90)
    return _cached_provider_call(
        key, lambda: fetch_price_and_volatility(coingecko_id)
    )


def _fetch_macro_snapshot() -> MacroSnapshot | None:
    key = ("fred", id(fetch_macro_snapshot))
    return _cached_provider_call(key, fetch_macro_snapshot)


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


def _persist_macro_snapshot(conn: sqlite3.Connection, snapshot) -> None:
    """Best-effort persistence of a fetched macro snapshot into
    macro_observations (migration 019), for freshness scoring. Tolerates a
    minimal test schema without the table (same pattern as
    _load_resolution_date above)."""
    if not _table_exists(conn, "macro_observations"):
        return
    now = datetime.now(UTC).isoformat()
    rows = [
        ("FEDFUNDS", snapshot.policy_rate_as_of, snapshot.policy_rate, getattr(snapshot, "policy_rate_source", "fred")),
        ("CPIAUCSL_YOY", getattr(snapshot, "cpi_yoy_as_of", None) or snapshot.as_of_date, snapshot.cpi_yoy, getattr(snapshot, "cpi_source", "fred")),
        ("CPIAUCSL_YOY_PRIOR", getattr(snapshot, "cpi_yoy_prior_as_of", None), snapshot.cpi_yoy_prior, getattr(snapshot, "cpi_source", "fred")),
        ("UNRATE", getattr(snapshot, "unemployment_rate_as_of", None) or snapshot.as_of_date, snapshot.unemployment_rate, getattr(snapshot, "unemployment_source", "fred")),
        ("UNRATE_PRIOR", getattr(snapshot, "unemployment_rate_prior_as_of", None), snapshot.unemployment_rate_prior, getattr(snapshot, "unemployment_source", "fred")),
    ]
    has_source_id = any(row[1] == "source_id" for row in conn.execute("PRAGMA table_info(macro_observations)"))
    for series_id, observation_date, value, source_id in rows:
        if observation_date is None or value is None:
            continue
        if has_source_id:
            conn.execute(
                """INSERT INTO macro_observations (series_id, observation_date, value, fetched_at, source_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(series_id, observation_date) DO UPDATE SET
                     value=excluded.value, fetched_at=excluded.fetched_at, source_id=excluded.source_id""",
                (series_id, observation_date.isoformat(), value, now, source_id),
            )
        else:
            conn.execute(
                """INSERT INTO macro_observations (series_id, observation_date, value, fetched_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(series_id, observation_date) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at""",
                (series_id, observation_date.isoformat(), value, now),
            )
    conn.commit()


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


def _load_source_health(conn: sqlite3.Connection) -> dict[str, dict] | None:
    """Real provider-health rows (Phase O's `provider_health` table),
    shaped the same way data_gaps.calculate_data_gaps expects
    (`source_health[source_id]["state"]`/`["source_id"]`). Returns None
    (not {}) when the table doesn't exist at all — genuinely "we don't
    track provider health here" is a different fact than "we tracked it
    and every source is unhealthy", and calculate_data_gaps treats a bare
    {} the same as None for its NEWS_PRIMARY check, so this distinction is
    for callers/tests, not a behavior difference in the gap calculation.
    """
    if not _table_exists(conn, "provider_health"):
        return None
    rows = conn.execute(
        "SELECT source_id, last_success, last_failure, last_failure_reason, "
        "last_http_status, last_latency_ms, consecutive_failures, "
        "data_age_seconds, items_fetched, parse_failures FROM provider_health"
    ).fetchall()
    if not rows:
        return None
    health_by_source: dict[str, dict] = {}
    for row in rows:
        health = row_to_provider_health(row)
        health_by_source[health.source_id] = {"source_id": health.source_id, "state": health.state().value}
    return health_by_source


def _forecast_status(
    estimated_yes: float | None, independent_probability: float | None,
    submodel_estimates: list[SubmodelEstimate], confidence: float,
    proposition: MarketProposition | None = None,
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

    Part 4 (correctness pass, 2026-08): explicit "History-only safety rule".
    Belt-and-suspenders on top of history.py's own comparable-gating
    (Parts 1-3): even if history.py somehow still produced an
    `available=True` estimate, when History is the ONLY contributing
    submodel and the target market's own proposition is unparseable
    (event_type unknown and/or proposition_status == AMBIGUOUS), this
    forces NO_FORECAST rather than BASELINE_ONLY. A quantitative-looking
    probability must never be published for a market whose own resolution
    semantics were never understood, no matter how the historical baseline
    was computed.
    """
    if estimated_yes is None:
        return "NO_FORECAST"
    available_names = {s.name for s in submodel_estimates if s.available}
    specialized_names = available_names & set(ALL_SPECIALIZED_MODEL_NAMES)
    independent_names = (
        available_names & {"history", "independent_evidence"}
    ) | specialized_names
    price_anchored_names = available_names & {"momentum", "news", "event_relations"}

    # Momentum, news, and event-relations may add valuable context, but all
    # are price-anchored or explanatory.  They cannot by themselves create
    # a model shadow.  Returning BLENDED_FORECAST here used to make a
    # momentum-only estimate look like an independent forecast even though
    # model_hypothesis_probability was None.
    if independent_probability is None or not independent_names:
        return "NO_FORECAST"

    if independent_names == {"history"} and not price_anchored_names:
        if proposition is not None and (
            proposition.event_type is None or proposition.proposition_status == "AMBIGUOUS"
        ):
            return "NO_FORECAST"
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
    history_estimate, comparable_sample_size, observed_yes_rate, _history_uncertainty = compute_history_estimate(
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
    classified_category: str | None = None,
    as_of: datetime | None = None,
) -> PredictionResult:
    """`as_of`, when given, makes this a point-in-time-safe forecast for
    backtesting: every submodel that reads time-ordered data (history's
    resolved-market comparables, independent evidence's linked news) is
    restricted to data timestamped at/before `as_of`, and `as_of` replaces
    wall-clock "now" throughout (deadline phase, recency weighting, reaction
    lag). Defaults to real wall-clock time for the normal/live call path —
    existing callers are unaffected."""
    reasoning: list[str] = []
    now = as_of or datetime.now(UTC)

    # --- Deadline Engine -------------------------------------------------
    resolution_date = _load_resolution_date(conn, market_id)
    deadline_phase = classify_deadline_phase(now, resolution_date)
    deadline_weights = deadline_weights_for(deadline_phase)
    reasoning.append(f"Deadline-Phase: {deadline_phase} (News-Gewicht {deadline_weights.news_weight:.2f}).")

    # --- History submodel --------------------------------------------------
    history_estimate, comparable_sample_size, observed_yes_rate, history_uncertainty = compute_history_estimate(
        conn, category, provider, question=question, resolution_text=resolution_text, as_of=as_of,
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

    # --- Resolution Engine (ROUND-1, section 4) --------------------------
    # Additive, read-only: no probability computation happens here. See
    # resolution_semantics.py for the extraction logic and HANDOFF.md's
    # round-1 section for the audit of resolution_rules.py this builds on.
    resolution_semantics = extract_resolution_semantics(question, resolution_text, proposition)
    reasoning.append(
        f"Resolution semantics: measurement={resolution_semantics.measurement or 'unknown'}, "
        f"confidence={resolution_semantics.confidence:.2f}, "
        f"{len(resolution_semantics.ambiguities)} ambiguity reason(s)."
    )

    # Point-in-time safety: CoinGecko/FRED have no "as of a past date"
    # fetch mode in this codebase (confirmed — both providers/coingecko.py
    # and providers/fred.py only expose current-time fetches). Calling them
    # during a backtest (as_of is not None) would silently leak
    # today's/current price or rate data into a forecast dated in the
    # past. Rather than build that historical-fetch mode (a non-trivial,
    # separate provider change) or risk contamination, quant/macro markets
    # are excluded from point-in-time-safe forecasts: both fetches are
    # skipped whenever as_of is set, so the quant/macro specialized paths
    # correctly fall back to their no-data behavior for those markets.
    quant_current_price = None
    quant_daily_volatility = None
    attempted_provider_sources: list[str] = []
    live_provider_sources: list[str] = []
    if as_of is None and proposition.event_type in ("price_above", "price_below") and proposition.asset:
        coingecko_id = resolve_coingecko_id(proposition.asset)
        if coingecko_id:
            attempted_provider_sources.append("coingecko")
            price_data = _fetch_quant_snapshot(coingecko_id)
            if price_data is not None:
                live_provider_sources.append("coingecko")
                quant_current_price = price_data.current_price
                quant_daily_volatility = price_data.daily_volatility

    # Same pattern as the CoinGecko fetch above, for macro.py's real FRED-
    # derived quantitative rate-decision signal: only spend the HTTP calls
    # when the proposition could plausibly need it (rate_cut/rate_hike/
    # rate_hold — the three event types macro.py's quantitative fallback
    # actually uses; see macro.py's _QUANTITATIVE_EVENT_TYPES). Also
    # skipped during backtests (as_of is not None) — see point-in-time
    # safety note above.
    macro_snapshot = None
    if as_of is None and proposition.event_type in ("rate_cut", "rate_hike", "rate_hold"):
        attempted_provider_sources.append("fred")
        macro_snapshot = _fetch_macro_snapshot()
        if macro_snapshot is not None:
            live_provider_sources.append("fred")
            try:
                _persist_macro_snapshot(conn, macro_snapshot)
            except sqlite3.Error:
                # Best-effort persistence only (freshness-scoring cache);
                # never let a storage hiccup break the live forecast path.
                pass

    routing = route_to_specialized_model(
        proposition, question,
        current_price=quant_current_price,  # real price from CoinGecko free tier, or None if unavailable
        historical_volatility=quant_daily_volatility,  # real realized daily vol from CoinGecko history, or None
        resolution_date=resolution_date,  # real ISO end_date from markets table, not the regex-parsed
                                           # proposition.deadline text (see route_to_specialized_model docstring)
        macro_snapshot=macro_snapshot,  # real (or None if unfetchable) FRED snapshot, forwarded to macro.py
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
        [
            history_estimate,
            momentum_estimate,
            independent_evidence_estimate,
            event_relation_estimate,
        ] + specialized_available
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
    # J1 fix: aktualitaet used to be hardcoded to a flat 85.0 regardless of
    # actual data staleness (see git history for the removed "KNOWN
    # LIMITATION" comment). Now computed from real timestamps: independent
    # evidence's own per-item recency_weight (evidence.py, decayed from real
    # published_at) and the latest market_snapshots.captured_at (decayed
    # with a much shorter half-life, since price data goes stale far faster
    # than news topicality). See confidence.compute_freshness_score.
    _evidence_recency = [
        e.recency_weight
        for e in (*independent_evidence.evidence_for_yes, *independent_evidence.evidence_for_no)
    ]
    _latest_price_captured_at = price_points[-1].captured_at if price_points else None
    _price_is_primary = proposition.event_type in ("price_above", "price_below")
    aktualitaet, freshness_detail = compute_freshness_score(
        _evidence_recency, _latest_price_captured_at, now=now, price_signal_is_primary=_price_is_primary,
        structured_data_recency_score=100.0 if quant_current_price is not None else None,
    )
    reasoning.append(freshness_detail)

    dq = DataQualityBreakdown(
        vollstaendigkeit=90.0 if data_quality_report_score and data_quality_report_score >= 90 else 60.0,
        aktualitaet=aktualitaet,
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

    # --- J2/K1: genuine multi-dimensional composites (additive) -----------
    # Real dimensions (Kish ESS, Wilson-interval width, evidence relation
    # tiers, source quality/independence, structured-data availability,
    # divergence_audit's model-disagreement stdev, specialized-model
    # reliability tagging) replace the previous flat/coarse proxies. The
    # legacy `dq`/`confidence` above are kept byte-for-byte for existing
    # `.data_quality.total`/`.confidence_score` consumers (grep across src/
    # shows opportunities.py, shadow_trading.py, ai/fallback.py, cli.py all
    # read these two fields expecting a 0..100 float) — engine.py now ALSO
    # computes and attaches the honest composites additively, and uses the
    # K1 composite as the production confidence_score (data_quality.total
    # stays as the legacy 6-field average since it feeds shadow_trading's
    # tuned thresholds; the new composite is the honest, structured version
    # surfaced alongside it via data_quality_composite/confidence_composite).
    data_quality_composite = compute_data_quality_composite(
        proposition=proposition, history_uncertainty=history_uncertainty,
        comparable_sample_size=comparable_sample_size, independent_evidence=independent_evidence,
        specialized_estimates=specialized_estimates, eligible_specialized_models=routing.eligible_models,
        aktualitaet=aktualitaet, resolution_semantics=resolution_semantics,
        live_provider_sources=tuple(live_provider_sources),
        attempted_provider_sources=tuple(attempted_provider_sources),
    )
    confidence_composite = compute_confidence_composite(
        proposition=proposition, history_uncertainty=history_uncertainty,
        comparable_sample_size=comparable_sample_size, independent_evidence=independent_evidence,
        specialized_estimates=specialized_estimates, all_submodel_estimates=all_submodels,
        aktualitaet=aktualitaet, deadline_phase_known=resolution_date is not None,
        legacy_data_quality=dq, resolution_semantics=resolution_semantics,
        live_provider_sources=tuple(live_provider_sources),
        attempted_provider_sources=tuple(attempted_provider_sources),
    )
    # K1: the composite score IS the production confidence_score — this is
    # the actual rebuild, not just an additive side-channel. The legacy
    # compute_confidence() call above is kept only because tests/test_prediction_v2.py
    # exercises it directly as a unit (coverage/agreement/stability
    # heuristic); it no longer determines PredictionResult.confidence_score.
    confidence = confidence_composite.score

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

    _prior_forecast_status_available_names = {s.name for s in all_submodels if s.available}
    _prior_forecast_status_was_history_only = (
        _prior_forecast_status_available_names & {"history", "independent_evidence"} == {"history"}
        and not (
            _prior_forecast_status_available_names
            & ({"momentum", "news", "event_relations"} | set(ALL_SPECIALIZED_MODEL_NAMES))
        )
    )
    # Part 4's history-only safety rule only applies when a real target
    # question was actually supplied (production callers — ai/service.py —
    # always pass real question text; `question` defaults to "" only for
    # legacy/back-compat callers and tests that predate proposition
    # parsing entirely and never intended to exercise it). With an empty
    # question there is nothing to have failed to parse, so `proposition`
    # is passed as None to _forecast_status in that case rather than
    # treating "no question was given" as "the question was ambiguous".
    _proposition_for_status = proposition if question else None
    forecast_status = _forecast_status(
        estimated_yes, independent_probability, all_submodels, confidence, _proposition_for_status
    )
    # A status must never imply a blended/usable forecast when the ensemble
    # did not produce a probability at all.  Some sparse live routes expose
    # available diagnostic submodels but no numeric estimate; without this
    # guard they were labelled BLENDED_FORECAST despite all forecast tiers
    # being null.  This is a semantic correction only: it creates no value,
    # relaxes no gate, and keeps the honest NO_FORECAST result.
    if estimated_yes is None:
        forecast_status = "NO_FORECAST"
    if forecast_status == "NO_FORECAST" and _prior_forecast_status_was_history_only and estimated_yes is not None:
        # History-only-safety-rule fired (Part 4): don't let a quantitative
        # probability survive as estimated_yes/independent_probability once
        # the forecast has been demoted to NO_FORECAST for this reason —
        # downstream recommendation logic must see "nothing to recommend",
        # not a number that happens to be unlabeled.
        independent_probability = None
        estimated_yes = None
        blended_probability = None
        calibrated_probability = None
        recommendation = "INSUFFICIENT_DATA"
        uncertainty_lower = uncertainty_upper = None

    # K3: prior provenance per submodel, surfaced in contribution_breakdown
    # so a future frontend can visibly distinguish "computed from real
    # observed outcomes" from "a reasoned heuristic" from "a structural
    # default that evidence then moved". See types.PriorProvenance.
    #   history: a real weighted baseline over actually-resolved comparable
    #     markets (history.py) -> DATA_FITTED.
    #   independent_evidence: starts from the neutral 0.5 Bayesian prior,
    #     which is a structural default, not a claim about the world
    #     (evidence.py) -> FALLBACK. When the extraordinary-event guard
    #     anchors to base_rates.py instead, that anchor is a documented,
    #     reasoned-but-not-statistically-fitted number -> EXPERT_HEURISTIC;
    #     since a single ContributionEntry can't carry both, FALLBACK is
    #     reported as the base case (the guard only fires for a minority of
    #     extraordinary-event-type markets — see evidence.py's
    #     extraordinary_guard_applied flag for the per-market truth).
    #   event_relations: starts from the market price itself when available
    #     (not a prior in the base-rate sense) -> UNKNOWN (not tracked).
    #   momentum/news: not prior-based (adjust an existing estimate rather
    #     than anchor one) -> None (no prior_provenance concept applies).
    #   specialized models: mix of structured-data-derived and heuristic
    #     confidence math depending on the model -> UNKNOWN today (not yet
    #     audited per-model; see K3 report for this honestly-left gap).
    _PRIOR_PROVENANCE_BY_SOURCE: dict[str, str] = {
        "history": "DATA_FITTED",
        "independent_evidence": "FALLBACK",
        "event_relations": "UNKNOWN",
    }

    # --- Divergence red-team audit (Phase M) -------------------------------
    # Replaces Phase B4's binary evidence_is_strong bool with a real,
    # itemized audit (see divergence_audit.py for the per-check breakdown
    # and verdict logic). REJECT behaves exactly like the old suppression
    # path (status -> FORECAST_SUPPRESSED, recommendation blocked);
    # WARN leaves the forecast standing but attaches the audit for
    # visibility; PASS attaches the audit with no suppression. The audit is
    # only even invoked (by audit_divergence itself) when the gap exceeds
    # DIVERGENCE_THRESHOLD_PP — a small gap never triggers it, matching the
    # old behavior exactly.
    audit_context = DivergenceAuditContext(
        independent_probability=independent_probability,
        market_probability=market_yes,
        proposition=proposition,
        independent_evidence=independent_evidence,
        comparable_sample_size=comparable_sample_size,
        history_prior_provenance=(
            _PRIOR_PROVENANCE_BY_SOURCE.get("history") if history_estimate.available else "UNKNOWN"
        ),
        resolution_rules_present=resolution_rules_present,
        submodel_estimates=tuple(all_submodels),
    )
    divergence_audit = audit_divergence(audit_context)
    divergence_support = classify_divergence_support(divergence_audit)
    forecast_suppression_reason: str | None = None
    if divergence_audit.verdict == "REJECT":
        # Part 2 (Block D): the project owner's exact required user-facing
        # (German) reason string is prepended so it is really reachable via
        # PredictionResult.forecast_suppression_reason -> as_dict() -> the
        # API response, not merely documented as "what the UI should show".
        # The English technical detail is kept appended for engineers/audit
        # trails — both are real, honest text, never fabricated.
        forecast_suppression_reason = (
            "Große Modellabweichung, derzeit nicht ausreichend unabhängig belegt. "
            f"(Forecast suppressed: independent estimate diverges from the market price by "
            f"{divergence_audit.gap:.1%}, exceeding the {DIVERGENCE_THRESHOLD_PP:.0%} safety threshold, and "
            f"the Phase M red-team audit returned REJECT — {divergence_audit.summary} "
            f"Failing checks: {[c.name for c in divergence_audit.checks if c.verdict == 'REJECT']}.)"
        )
        reasoning.append(forecast_suppression_reason)
        forecast_status = "FORECAST_SUPPRESSED"
    elif divergence_audit.verdict == "WARN":
        reasoning.append(
            f"Divergence audit WARN (not suppressed): {divergence_audit.summary} "
            f"Flagged checks: {[c.name for c in divergence_audit.checks if c.verdict == 'WARN']}."
        )

    # Part 5 (correctness pass, 2026-08): structural gate re-applied here
    # (after divergence_audit may have just set FORECAST_SUPPRESSED) — a
    # STRONG_YES/STRONG_NO recommendation must be structurally impossible
    # whenever forecast_status is NO_FORECAST/FORECAST_SUPPRESSED, no matter
    # how `recommendation` was derived earlier from net_edge/confidence.
    if forecast_status in ("NO_FORECAST", "FORECAST_SUPPRESSED") and recommendation in ("STRONG_YES", "STRONG_NO"):
        recommendation = "INSUFFICIENT_DATA"

    total_available_weight = sum(s.weight for s in all_submodels if s.available)
    # Block D Part 1: `news` is the one submodel here whose
    # estimated_yes_probability never actually enters ensemble.
    # combine_submodels' weighted average (it moves the final estimate via a
    # separate Bayesian update on weighted_sentiment/confirmation_count
    # instead — see the news+Bayesian block above) — a "contribution_pp"
    # figure for it would be a decorative number implying math that isn't
    # really happening, so it is honestly reported as None; influence_rank
    # (always computed from the same real probability/weight_share pair)
    # takes over as the honest signal for that case.
    _NO_CLEAN_PP_ATTRIBUTION = {"news"}
    contribution_breakdown = tuple(
        ContributionEntry(
            source=s.name,
            available=s.available,
            estimated_yes_probability=s.estimated_yes_probability,
            weight_share=(round(s.weight / total_available_weight, 4) if s.available and total_available_weight > 0 else None),
            detail=s.detail,
            direction=(
                "YES" if s.estimated_yes_probability is not None and s.estimated_yes_probability > 0.5
                else "NO" if s.estimated_yes_probability is not None and s.estimated_yes_probability < 0.5
                else "neutral"
            ) if s.available and s.estimated_yes_probability is not None else None,
            contribution_pp=(
                round((s.estimated_yes_probability - 0.5) * (s.weight / total_available_weight), 4)
                if s.available and s.estimated_yes_probability is not None and total_available_weight > 0
                and s.name not in _NO_CLEAN_PP_ATTRIBUTION
                else None
            ),
            influence_rank=_classify_influence_rank(
                s.estimated_yes_probability,
                (s.weight / total_available_weight) if s.available and total_available_weight > 0 else None,
            ) if s.available else None,
            source_ids=(
                tuple(str(e.news_event_id) for e in (*independent_evidence.evidence_for_yes, *independent_evidence.evidence_for_no))
                if s.name == "independent_evidence" and independent_evidence.available else ()
            ),
            evidence_strength=(
                "hoch" if s.name == "independent_evidence" and independent_evidence.source_quality_score is not None and independent_evidence.source_quality_score >= 70 else
                "mittel" if s.name == "independent_evidence" and independent_evidence.source_quality_score is not None and independent_evidence.source_quality_score >= 40 else
                "gering" if s.name == "independent_evidence" and independent_evidence.source_quality_score is not None else
                None
            ),
            calculation_method=(
                "weighted_average" if s.available else None
            ),
            explanation=s.detail,
            eligible=specialized_eligibility.get(s.name),
            prior_provenance=_PRIOR_PROVENANCE_BY_SOURCE.get(
                s.name, "UNKNOWN" if s.name in ALL_SPECIALIZED_MODEL_NAMES else None
            ),
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

    # --- Data Gap Engine (Phase O, connected) ------------------------------
    # calculate_data_gaps (data_gaps.py) is Qwen's real gap-detection logic;
    # it existed but was only ever wired into a standalone, dead API endpoint
    # (`GET /data-gaps/{market_id}`, which fed it hardcoded placeholders —
    # comparable_count=0, has_event_relations=False, event_type=None — and
    # called a Storage method, `get_all_provider_health`, that does not
    # exist, so that endpoint would crash if ever hit). Here it is fed the
    # engine's own real, already-computed values instead, so the report
    # reflects this specific prediction run rather than fabricated
    # placeholders. Purely diagnostic/additive: it is computed from values
    # already used elsewhere in this function and never feeds back into
    # estimated_yes/independent_probability/confidence.
    # Use the Phase-C classified taxonomy value (CENTRAL_BANKS/GEOPOLITICS/
    # POLITICS/...) here, NOT the raw `category` param — that's the
    # unfiltered `markets.category` DB column, which for real markets is
    # frequently junk (sometimes literally the question text). calculate_
    # data_gaps gates its NEWS_PRIMARY/STRUCTURED_DATA gap messages on exact
    # matches against the classified enum, so passing the raw column meant
    # those gaps could never fire. Falls back to `category` only if no
    # classification exists yet, so this stays backward compatible.
    # Computed BEFORE calculate_data_gaps (reordered from the original
    # sequence) specifically so Block D Part 3 can pass the real
    # ResolutionPath (world_state.path_to_resolution, Block C's
    # ResolutionStep/ResolutionPath structure) into the Data Gap Engine for
    # concrete, step-named gap descriptions instead of only generic ones.
    # assemble_world_state does not depend on anything calculate_data_gaps
    # computes, so this reorder is behavior-preserving for world_state itself.
    world_state = assemble_world_state(
        proposition=proposition, resolution_date=resolution_date, now=now,
        independent_evidence=independent_evidence,
        classified_category=classified_category,
        # ROUND-2 (section 5): the SAME already-fetched FRED/CoinGecko
        # values forwarded to macro.py/quant.py above — not fetched again.
        # Both are None for every market outside MACRO/CRYPTO (see the
        # gating around their original fetch calls above), which correctly
        # produces an empty state_variables tuple for those markets.
        macro_snapshot=macro_snapshot,
        quant_asset=proposition.asset,
        quant_current_price=quant_current_price,
        quant_daily_volatility=quant_daily_volatility,
    )

    data_gaps = calculate_data_gaps(
        market_id=market_id,
        question=question or "",
        market_category=classified_category or category,
        event_type=proposition.event_type,
        source_health=_load_source_health(conn),
        historical_comparables_count=comparable_sample_size,
        # Not tracked anywhere in the engine today (no per-market horizon-
        # compatibility computation exists yet — history.py's similarity
        # scoring blends horizon into one composite score rather than
        # exposing a standalone bool) — honestly reported as unknown rather
        # than guessed at.
        time_horizon_compatible=None,
        has_structured_data=len(specialized_available) > 0,
        has_event_relations=len(event_relation_signals) > 0,
        # Block D Part 3: real ResolutionPath, computed above from Block C's
        # structure — None/applies=False for the overwhelming majority of
        # markets with no known multi-step resolution structure, in which
        # case calculate_data_gaps falls back to its pre-existing generic
        # gap descriptions unchanged.
        resolution_path=(
            world_state.path_to_resolution.resolution_path
            if world_state is not None and world_state.path_to_resolution is not None
            else None
        ),
    )

    # Phase E: Structured World State -- the single compact summary every
    # consumer (explanation, scenarios, research queue, UI) should read
    # instead of re-deriving its own view. Pure composition of world_state/
    # data_gaps already computed above, no new computation.
    structured_world_state = assemble_structured_world_state(
        world_state=world_state,
        resolution_path=(
            world_state.path_to_resolution.resolution_path
            if world_state is not None and world_state.path_to_resolution is not None
            else None
        ),
        data_gap_report=data_gaps,
    )

    # Phase F: Next Event Engine -- the most likely next resolution-relevant
    # event, derived purely from the same real ResolutionPath used above
    # plus the real PATH_STEP claims (for supporting_claim_ids/source_ids).
    # Never an LLM guess; UNKNOWN/None is the honest default for the
    # majority of markets with no known multi-step template.
    next_event = derive_next_event(
        resolution_path=(
            world_state.path_to_resolution.resolution_path
            if world_state is not None and world_state.path_to_resolution is not None
            else None
        ),
        path_step_claims=independent_evidence.path_step_claims if independent_evidence is not None else (),
    )

    # Phase G: Event Clock -- can a possible future path still happen in
    # time. Reuses world_state's own deadline/time_remaining_hours and
    # resolution_path's own deadline_pressure/path_feasibility; no new
    # duration data invented.
    event_clock = derive_event_clock(
        deadline=world_state.deadline if world_state is not None else None,
        time_remaining_hours=world_state.time_remaining_hours if world_state is not None else None,
        resolution_path=(
            world_state.path_to_resolution.resolution_path
            if world_state is not None and world_state.path_to_resolution is not None
            else None
        ),
        next_event=next_event,
    )

    # Phase H: Expected vs Observed -- whether the previously-expected step
    # was actually observed, and whether the currently-expected one is
    # running late against the market's own real deadline. Reuses
    # event_clock's real feasibility/deadline-pressure signals; no new
    # duration data invented.
    expected_vs_observed = derive_expected_vs_observed(
        resolution_path=(
            world_state.path_to_resolution.resolution_path
            if world_state is not None and world_state.path_to_resolution is not None
            else None
        ),
        next_event=next_event,
        event_clock=event_clock,
    )

    # Phase I: Conditional Transition Engine -- the remaining resolution
    # path as a chain of prerequisite->transition entries. Honestly
    # QUALITATIVE_ONLY for every entry (no real transition-rate dataset
    # exists), never a fabricated conditional probability.
    conditional_transitions = derive_conditional_transitions(
        resolution_path=(
            world_state.path_to_resolution.resolution_path
            if world_state is not None and world_state.path_to_resolution is not None
            else None
        ),
    )

    # Phase J: branch view over the exact same state/path/transition objects.
    scenario_tree = derive_scenario_tree(
        resolution_path=(
            world_state.path_to_resolution.resolution_path
            if world_state is not None and world_state.path_to_resolution is not None
            else None
        ),
        structured_world_state=structured_world_state,
        conditional_transitions=conditional_transitions,
        event_clock=event_clock,
        contradicting_claims=tuple(
            factor.title for factor in (independent_evidence.evidence_for_no if independent_evidence else ())[:5]
        ),
    )
    sensitivity_audit = derive_sensitivity_audit(tuple(all_submodels))

    # Block F Part 1: compute change_triggers HERE (moved up from its
    # original post-result location — world_state/data_gaps/divergence_audit
    # are all already real values by this point) so build_scenarios can
    # reuse the SAME real trigger strings rather than a second, later,
    # duplicate computation. The later `change_triggers = compute_change_
    # triggers(...)` call below is removed in favor of this single value.
    _resolution_path_for_scenarios = (
        world_state.path_to_resolution.resolution_path
        if world_state is not None and world_state.path_to_resolution is not None
        else None
    )
    change_triggers = compute_change_triggers(
        world_state=world_state,
        data_gaps=data_gaps,
        divergence_audit=divergence_audit,
    )
    scenarios = build_scenarios(
        estimated_yes_probability=estimated_yes, submodel_estimates=all_submodels,
        news_evidence=news_evidence, comparable_sample_size=comparable_sample_size,
        recommendation=recommendation,
        resolution_path=_resolution_path_for_scenarios,
        resolution_semantics=resolution_semantics,
        evidence_for_yes=independent_evidence.evidence_for_yes if independent_evidence else (),
        evidence_for_no=independent_evidence.evidence_for_no if independent_evidence else (),
        change_triggers=change_triggers,
    )

    result = PredictionResult(
        market_id=market_id,
        market_yes_probability=market_yes,
        market_no_probability=market_no,
        estimated_yes_probability=estimated_yes,
        estimated_no_probability=estimated_no,
        gross_yes_edge=gross_edge,
        net_yes_edge=net_edge,
        confidence_score=confidence,
        data_quality=dq,
        data_quality_composite=data_quality_composite,
        confidence_composite=confidence_composite,
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
        market_probability=market_yes,
        # Block A: model_hypothesis_probability is the raw numeric
        # target-specific domain-model opinion. It is distinct from generic
        # market-blind history/news/claim context and does NOT imply it is
        # publishable; see evidence_backed_probability /
        # published_forecast_probability below.
        # A model shadow is a numeric domain-model result, not merely a
        # generic evidence/history ensemble.  The latter is still valuable
        # context for maturity and suppression decisions, but cannot claim a
        # target-specific probability for a multi-step proposition.
        model_hypothesis_probability=(
            independent_probability if specialized_available else None
        ),
        evidence_backed_probability=None,
        published_forecast_probability=None,
        forecast_status=forecast_status,
        contribution_breakdown=contribution_breakdown,
        forecast_suppression_reason=forecast_suppression_reason,
        divergence_audit=divergence_audit,
        divergence_support=divergence_support,
        historical_comparables=(
            tuple(history_uncertainty.top_comparable_cases) if history_uncertainty is not None else ()
        ),
        historical_candidate_count=history_uncertainty.candidate_count if history_uncertainty is not None else 0,
        historical_accepted_count=history_uncertainty.accepted_count if history_uncertainty is not None else 0,
        historical_rejected_count=history_uncertainty.rejected_count if history_uncertainty is not None else 0,
        data_gaps=data_gaps,
        world_state=world_state,
        proposition=proposition,
        resolution_semantics=resolution_semantics,
        structured_world_state=structured_world_state,
        next_event=next_event,
        event_clock=event_clock,
        expected_vs_observed=expected_vs_observed,
        conditional_transitions=conditional_transitions,
        scenario_tree=scenario_tree,
        sensitivity_audit=sensitivity_audit,
    )
    # Phase F: evidence-gated forecast hierarchy
    # evidence_backed_probability: only when sufficient evidence exists
    # published_forecast_probability: only when not suppressed
    maturity = classify_forecast_maturity(result)
    # Block A gate:
    #   evidence_backed_probability: real DIRECT/SUPPORTS-tier evidence with
    #     adequate comparables exists (maturity reached at least
    #     PARTIAL_FORECAST — a real, non-thin estimate — up through
    #     MATURE_FORECAST). NO_FORECAST / CONTEXT_ONLY / HYPOTHESIS all mean
    #     "not enough evidence yet" -> None. This is deliberately more lenient
    #     than the publish gate below: a real-but-incomplete forecast (data
    #     gaps present) is still evidence-backed, just not yet publishable.
    #   published_forecast_probability (exact spec): None whenever
    #     forecast_maturity is below SUPPORTED_FORECAST, or the divergence
    #     audit verdict is REJECT (folded into forecast_status ==
    #     FORECAST_SUPPRESSED by classify_forecast_maturity), or
    #     evidence_backed_probability is None.
    evidence_backed_probability = (
        result.independent_probability
        if maturity in ("PARTIAL_FORECAST", "SUPPORTED_FORECAST", "MATURE_FORECAST")
        else None
    )
    published_forecast_probability = (
        evidence_backed_probability
        if evidence_backed_probability is not None
        and maturity in ("SUPPORTED_FORECAST", "MATURE_FORECAST")
        and result.forecast_status not in ("NO_FORECAST", "FORECAST_SUPPRESSED")
        and (result.divergence_audit is None or result.divergence_audit.verdict != "REJECT")
        else None
    )
    # recommendation: only INSUFFICIENT_DATA when NO_FORECAST or FORECAST_SUPPRESSED
    # otherwise keep the computed recommendation from earlier in the function
    recommendation = (
        "INSUFFICIENT_DATA"
        if maturity == "NO_FORECAST" or result.forecast_status == "FORECAST_SUPPRESSED"
        else result.recommendation
    )
    # Block D Part 4 / Block F Part 1: `change_triggers` was already computed
    # earlier in this function (above, before build_scenarios) from the same
    # world_state/data_gaps/divergence_audit values now sitting on `result`
    # — reused here rather than recomputed a second time.
    # Block E Part 1: Decision Engine. Computed from the just-finalized
    # forecast_maturity/published_forecast_probability above — `result` at
    # this point still has the PRE-Part-1 maturity/published values, so we
    # build a temporary view with the finalized values for the decision
    # function rather than re-deriving them. `spread` is not available at
    # this layer (compute_prediction has no spread parameter) — callers
    # with real market_snapshots spread data (opportunities.py) should
    # re-call prediction.decision.compute_decision_state directly with the
    # real spread for a more precise cap; engine-level decision_state is
    # computed with spread=None (never wrongly downgrades, may be
    # over-optimistic about tradeability in illiquid/wide-spread markets).
    _decision_input = _dataclass_replace(
        result,
        forecast_maturity=maturity,
        published_forecast_probability=published_forecast_probability,
    )
    decision_state, decision_reasons = compute_decision_state(
        _decision_input,
        liquidity=liquidity,
        spread=None,
        deadline_hours=(resolution_date - now).total_seconds() / 3600 if resolution_date else None,
    )
    return _dataclass_replace(
        result,
        forecast_maturity=maturity,
        maturity_breakdown=build_maturity_breakdown(result),
        recommendation=recommendation,
        evidence_backed_probability=evidence_backed_probability,
        published_forecast_probability=published_forecast_probability,
        change_triggers=change_triggers,
        decision_state=decision_state,
        decision_reasons=decision_reasons,
    )
