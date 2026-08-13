from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..data_gaps import DataGapReport
    from .conditional_transitions import ConditionalTransition
    from .cross_market import CrossMarketResult
    from .divergence_audit import DivergenceAuditResult
    from .event_clock import EventClock
    from .event_relations import RelationSignal
    from .evidence import IndependentEvidenceResult
    from .expected_vs_observed import ExpectedVsObserved
    from .manipulation import ManipulationRiskResult
    from .market_flow import OrderBookMetrics, TradeFlowMetrics, WalletConcentrationMetrics
    from .next_event import NextEvent
    from .reaction_lag import ReactionLagResult
    from .reliability import MarketReliabilityResult
    from .resolution_edge import ResolutionEdgeResult
    from .resolution_semantics import ResolutionSemantics
    from .scenario_tree import ScenarioTree
    from .semantics import MarketProposition
    from .sensitivity import SensitivityAudit
    from .structured_state import StructuredWorldState
    from .world_state import WorldState

Recommendation = Literal[
    "STRONG_YES", "YES", "WATCH_YES", "NO_BET", "WATCH_NO", "NO", "STRONG_NO", "INSUFFICIENT_DATA"
]

# K3: tags every "prior"/base-rate-like starting point in the engine with
# its real provenance, so a reasoned-but-not-statistically-fitted number
# (e.g. base_rates.py's manually-authored table) can never be presented as
# if it were computed from real observed outcomes.
#   DATA_FITTED      computed from real observed historical outcomes with a
#                     real, reportable sample size (e.g. history.py's
#                     weighted comparable-case baseline).
#   EXPERT_HEURISTIC  a documented, reasoned-but-not-statistically-fitted
#                     number (e.g. base_rates.py's manually-reasoned table).
#   FALLBACK          a structural default (e.g. evidence.py's neutral 0.5
#                     Bayesian starting point) that then gets moved by real
#                     evidence — not itself a claim about the world.
#   UNKNOWN           provenance genuinely not tracked for this value.
PriorProvenance = Literal["DATA_FITTED", "EXPERT_HEURISTIC", "FALLBACK", "UNKNOWN"]

ConfidenceCalibrationStatus = Literal["UNCALIBRATED", "CALIBRATED"]

# K1b: the confidence score has no real out-of-sample resolved-shadow-forecast
# data to calibrate against yet (that is Phase N/N2's job: collect resolved
# forecasts, compute Brier/reliability curves, and fit an actual calibration
# mapping). Until that data exists, every PredictionResult is honestly
# tagged UNCALIBRATED — a literal, not a computed number pretending to be
# one. Phase N2 will replace this constant with a real per-market lookup
# once resolved-forecast history exists to compute it from.
DEFAULT_CONFIDENCE_CALIBRATION_STATUS: ConfidenceCalibrationStatus = "UNCALIBRATED"

ForecastStatus = Literal[
    "NO_FORECAST", "BASELINE_ONLY", "EVIDENCE_ONLY", "LOW_DATA", "INDEPENDENT_FORECAST", "BLENDED_FORECAST",
    # Phase B4: independent estimate diverged sharply from the market price
    # without evidence strong enough to justify it — suppressed rather than
    # reported as a fabricated-looking number. See prediction/divergence.py.
    "FORECAST_SUPPRESSED",
]

# Forecast Maturity taxonomy (steering point 14): a coarse, honest label
# for "how much should a reader trust this specific forecast", derived from
# signals the engine already computes (forecast_status, confidence/data-
# quality composites, evidence-tier mix, divergence-audit verdict, data-gap
# severity). See prediction/maturity.py for the exact thresholds and the
# EXPERT_HEURISTIC provenance note (no resolved-outcome history exists yet
# to calibrate these cutoffs against).
ForecastMaturity = Literal[
    "NO_FORECAST",
    "CONTEXT_ONLY",
    "HYPOTHESIS",
    "PARTIAL_FORECAST",
    "SUPPORTED_FORECAST",
    "MATURE_FORECAST",
]

# Block E, Part 1: Decision Engine states. Distinct from ForecastMaturity
# (maturity answers "how much should a reader trust THIS forecast";
# DecisionState answers "is there an actionable edge worth surfacing").
# Ordered weakest -> strongest. See prediction/decision.py for the
# weighting logic and the hard rule: a large model_hypothesis deviation
# ALONE (published_forecast_probability=None) can never exceed WATCH.
DecisionState = Literal["NO_POSITION", "WATCH", "POSSIBLE_EDGE", "STRONG_EDGE"]

PREDICTION_VERSION = "v2"


@dataclass(frozen=True)
class ContributionEntry:
    """One line of the forecast's contribution breakdown — every submodel
    that could have contributed, whether it did or not. `available=False`
    means exactly that: this source had nothing to say, never silently
    treated as a zero-effect contribution."""

    source: str
    available: bool
    estimated_yes_probability: float | None
    weight_share: float | None  # this submodel's share of the ensemble's total weight, 0..1
    detail: str
    # Phase F: distinguishes "this specialized model was never a candidate
    # for this market's event_type/category" (eligible=False) from "it was
    # eligible but had no usable data for this specific market"
    # (eligible=True, available=False). None for the always-eligible
    # generic submodels (history, momentum, news, independent_evidence,
    # event_relations), which every market is a candidate for by
    # construction — eligibility is a meaningful distinction only for the
    # specialized (Phase E) router-selected models.
    eligible: bool | None = None
    # K3: provenance of the "prior" this contribution effectively acts as
    # (its starting point before evidence moves it) — None for submodels
    # that don't have a prior/base-rate concept at all (e.g. momentum).
    # See PriorProvenance in this module for the literal's meaning.
    prior_provenance: PriorProvenance | None = None
    # Phase F: contribution metadata for evidence-first presentation
    direction: str | None = None
    contribution_pp: float | None = None
    source_ids: tuple[str, ...] = field(default_factory=tuple)
    evidence_strength: str | None = None
    calculation_method: str | None = None
    explanation: str | None = None
    # --- Block D Part 1: influence-ranking (additive) -----------------------
    # `contribution_pp` above is REAL weighted-average pp math only for
    # submodels that actually participate in ensemble.combine_submodels'
    # linear weighted average feeding engine.py's `prior_estimate`/
    # `independent_probability` (history, momentum, independent_evidence,
    # event_relation, and the routed specialized model). It is NOT real for
    # `news`: the news submodel's estimated_yes_probability never enters
    # that weighted average at all — news instead moves the final number
    # via a separate Bayesian update (weighted_sentiment/confirmation_count,
    # see engine.py/bayesian_update) that has no clean per-pp decomposition
    # back onto a single "news contributed X pp" number. Rather than keep
    # presenting a decorative pp figure for that case, engine.py sets
    # `contribution_pp=None` for it and every reader instead gets this
    # honest, always-computed `influence_rank`: a coarse but real signal
    # derived from (a) how far estimated_yes_probability sits from the
    # neutral 0.5 midpoint (direction/strength of the submodel's own
    # opinion) and (b) its actual weight_share in the ensemble (how much
    # that opinion actually mattered) — never an arbitrary/invented label.
    # See engine.py's `_classify_influence_rank` for the exact thresholds.
    influence_rank: str | None = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "available": self.available,
            "estimated_yes_probability": self.estimated_yes_probability,
            "weight_share": self.weight_share,
            "detail": self.detail,
            "eligible": self.eligible,
            "prior_provenance": self.prior_provenance,
            "direction": self.direction,
            "contribution_pp": self.contribution_pp,
            "source_ids": list(self.source_ids),
            "evidence_strength": self.evidence_strength,
            "calculation_method": self.calculation_method,
            "explanation": self.explanation,
            "influence_rank": self.influence_rank,
        }


@dataclass(frozen=True)
class SubmodelEstimate:
    """One ensemble member's independent opinion. `weight` is the *base*
    (pre-normalization) weight the ensemble assigns it; `available` marks
    whether the submodel had enough input to produce a real estimate at all
    (e.g. the history model with zero comparable cases) — unavailable
    submodels are excluded from the blend rather than silently defaulting
    to 0.5, which would quietly bias the ensemble."""

    name: str
    estimated_yes_probability: float | None
    weight: float
    available: bool
    detail: str


@dataclass(frozen=True)
class DataQualityBreakdown:
    """Every component behind the single data_quality_score, so it is never
    a bare number without explanation (per the dashboard requirement)."""

    vollstaendigkeit: float
    aktualitaet: float
    quellenuebereinstimmung: float
    historische_fallzahl: float
    resolution_klarheit: float
    liquiditaet: float

    @property
    def total(self) -> float:
        return round(
            (
                self.vollstaendigkeit
                + self.aktualitaet
                + self.quellenuebereinstimmung
                + self.historische_fallzahl
                + self.resolution_klarheit
                + self.liquiditaet
            )
            / 6,
            1,
        )

    def as_dict(self) -> dict:
        return {
            "vollstaendigkeit": self.vollstaendigkeit,
            "aktualitaet": self.aktualitaet,
            "quellenuebereinstimmung": self.quellenuebereinstimmung,
            "historische_fallzahl": self.historische_fallzahl,
            "resolution_klarheit": self.resolution_klarheit,
            "liquiditaet": self.liquiditaet,
            "gesamt": self.total,
        }


@dataclass(frozen=True)
class QualityDimension:
    """One measured dimension feeding a J2/K1 composite. `available=False`
    means this dimension is genuinely not computable for this market today
    (e.g. no independent evidence at all, or the legacy history path with no
    Kish-ESS concept) — it is excluded from the composite average and its
    weight is redistributed among the dimensions that ARE available, rather
    than silently defaulting to a flattering mid/high score. This is the
    direct fix for the "12 irrelevant articles inflate quality" bug class."""

    name: str
    raw_value: float | None
    normalized_score: float | None  # 0..100, None when not available
    available: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "name": self.name, "raw_value": self.raw_value,
            "normalized_score": self.normalized_score,
            "available": self.available, "reason": self.reason,
        }


@dataclass(frozen=True)
class QualityComposite:
    """Shared shape for both the J2 data_quality composite and the K1
    confidence composite: a real weighted average over whichever dimensions
    were actually computable this call, plus the full per-dimension detail
    so a UI/audit can see exactly what fed the number and what didn't."""

    dimensions: tuple[QualityDimension, ...]
    score: float  # 0..100
    formula_detail: str

    def as_dict(self) -> dict:
        return {
            "dimensions": [d.as_dict() for d in self.dimensions],
            "score": self.score,
            "formula_detail": self.formula_detail,
        }


@dataclass(frozen=True)
class Scenario:
    """Block F Part 1: one genuinely-derived YES/NO scenario. Every field
    is traceable to real, already-computed structured data — ResolutionPath
    steps (Block C), real evidence/claims (EvidenceFactor titles from
    evidence.py), and change_triggers (Block D Part 4). `probability` is
    None unless a real per-scenario number was actually computed somewhere
    (no code path fabricates one today, so this is the honest default for
    every scenario currently produced)."""

    outcome: str  # "YES" | "NO"
    description: str
    necessary_events: tuple[str, ...] = field(default_factory=tuple)
    supporting_claims: tuple[str, ...] = field(default_factory=tuple)
    contradicting_claims: tuple[str, ...] = field(default_factory=tuple)
    triggers: tuple[str, ...] = field(default_factory=tuple)
    probability: float | None = None

    def as_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "description": self.description,
            "necessary_events": list(self.necessary_events),
            "supporting_claims": list(self.supporting_claims),
            "contradicting_claims": list(self.contradicting_claims),
            "triggers": list(self.triggers),
            "probability": self.probability,
        }


@dataclass(frozen=True)
class ScenarioSet:
    """Deterministic, factor-derived scenario descriptions. Text is built
    from structured inputs (submodel estimates, news events, deadline
    phase) by plain string templates — no LLM involved in deciding what the
    scenarios *are*; GPT is only ever handed this finished set to phrase
    more naturally in the explanation layer.

    `scenarios` (Block F Part 1, additive) is the genuinely-derived YES/NO
    scenario pair described above — real ResolutionPath structure for
    markets that have one (e.g. legislation), or a minimal honest
    yes_condition/no_condition pair for simple binary markets, or an empty
    tuple when neither exists. `base_case`/`bull_case`/`bear_case` are kept
    unchanged for backward compatibility with existing callers/tests."""

    base_case: str
    bull_case: list[str]
    bear_case: list[str]
    scenarios: tuple[Scenario, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "base_case": self.base_case,
            "bull_case": self.bull_case,
            "bear_case": self.bear_case,
            "scenarios": [s.as_dict() for s in self.scenarios],
        }


@dataclass(frozen=True)
class PredictionResult:
    """The statistical engine's binding, unmodifiable output. GPT is only
    ever allowed to *explain* these numbers — never to change or invent
    them (enforced by ai/validation.py).

    V2 extends the V1 (base-rate-only) result with an ensemble of
    independent submodels (deadline/momentum/history/news+Bayesian),
    scenario framing, and full submodel transparency — every existing V1
    field is preserved so downstream consumers (ai/service.py, backtest.py,
    the dashboard, existing tests) keep working unchanged; new fields are
    purely additive.
    """

    market_id: str
    market_yes_probability: float | None
    market_no_probability: float | None
    estimated_yes_probability: float | None
    estimated_no_probability: float | None
    gross_yes_edge: float | None
    net_yes_edge: float | None
    confidence_score: float
    data_quality: DataQualityBreakdown
    uncertainty_lower: float | None
    uncertainty_upper: float | None
    recommendation: Recommendation
    comparable_sample_size: int
    observed_historical_yes_rate: float | None
    reasoning_notes: tuple[str, ...] = field(default_factory=tuple)

    # --- V2 additions (all optional / additive) -----------------------
    deadline_phase: str = "unbekannt"
    submodel_estimates: tuple[SubmodelEstimate, ...] = field(default_factory=tuple)
    ensemble_agreement: float | None = None
    scenarios: ScenarioSet | None = None
    news_sentiment_score: float | None = None  # -1 (negativ) .. +1 (positiv)
    news_confirmation_count: int = 0

    # --- Independent Evidence & Early-Signal Engine (additive) ---------
    independent_evidence: IndependentEvidenceResult | None = None

    # --- Structural edge analysis (additive) ----------------------------
    resolution_edge: ResolutionEdgeResult | None = None
    cross_market: CrossMarketResult | None = None
    reaction_lag: ReactionLagResult | None = None

    # --- Public market-flow / order-book / wallet intelligence (additive) --
    orderbook_metrics: OrderBookMetrics | None = None
    trade_flow_metrics: TradeFlowMetrics | None = None
    wallet_concentration: WalletConcentrationMetrics | None = None
    market_reliability: MarketReliabilityResult | None = None
    manipulation_risk: ManipulationRiskResult | None = None

    # --- Event-Relation causal-reasoning foundation (additive) ---------
    event_relation_signals: tuple[RelationSignal, ...] = field(default_factory=tuple)

    # --- Independent vs. market vs. blended vs. calibrated (additive) ---
    # This is the architectural core the product needs to be able to answer
    # "did we build a forecasting machine or just an intelligent Polymarket
    # post-processor?": four DISTINCT numbers, never conflated.
    #   independent_probability: computed from ONLY the submodels that never
    #     take the market price as an input at all (history + independent
    #     evidence) — this is "what do we think without looking at the
    #     market?"
    #   market_consensus_probability: the market's own price (alias of
    #     market_yes_probability, kept alongside the other three so a caller
    #     never has to reach into a different field to compare all four).
    #   blended_probability: the full ensemble estimate (same value as
    #     estimated_yes_probability today) — includes the market-price-
    #     anchored submodels (momentum, news, event-relations adjustments).
    #   calibrated_probability: blended_probability shrunk toward the
    #     uninformative 0.5 prior in proportion to (100 - confidence) — a
    #     real, computed calibration step, not a placeholder equal to
    #     blended_probability. See engine.py for the exact formula.
    independent_probability: float | None = None
    market_consensus_probability: float | None = None
    blended_probability: float | None = None
    calibrated_probability: float | None = None
    # --- Block A: forecast-semantics separation (additive, four distinct
    # concepts — see INTEGRATION_PLAN.md / HANDOFF.md Block A) ---------------
    #   market_probability: Polymarket's own price. Pure alias of
    #     market_consensus_probability (no duplicate storage; see __post_init__).
    #   model_hypothesis_probability: the model's raw internal opinion
    #     (== independent_probability today). Existing does NOT imply it is
    #     trustworthy or publishable on its own.
    #   evidence_backed_probability: model_hypothesis_probability, but only
    #     when forecast_maturity has reached SUPPORTED_FORECAST or
    #     MATURE_FORECAST (real DIRECT/SUPPORTS-tier evidence, adequate
    #     comparables, no critical/high data gaps, divergence audit not
    #     REJECT) — None otherwise.
    #   published_forecast_probability: the actual publishable
    #     PolyMarketPulse forecast. None whenever evidence_backed_probability
    #     is None OR forecast_status in ("NO_FORECAST", "FORECAST_SUPPRESSED").
    #     This is the field downstream consumers (UI/API/opportunities) must
    #     gate on — never independent_probability/blended_probability directly.
    market_probability: float | None = None
    model_hypothesis_probability: float | None = None
    evidence_backed_probability: float | None = None
    published_forecast_probability: float | None = None
    forecast_status: ForecastStatus = "NO_FORECAST"
    contribution_breakdown: tuple[ContributionEntry, ...] = field(default_factory=tuple)
    # Phase B4: human-readable reason when forecast_status == "FORECAST_SUPPRESSED",
    # None otherwise. See prediction/divergence.py.
    forecast_suppression_reason: str | None = None

    # --- Phase M: itemized divergence red-team audit (additive) -----------
    # Populated whenever the divergence gap exceeded the threshold (whether
    # the resulting verdict was PASS, WARN, or REJECT/suppressed) — None
    # only when the gap never triggered the audit at all. See
    # prediction/divergence_audit.py.
    divergence_audit: DivergenceAuditResult | None = None
    # --- Part 4 (this round): thin relabeling of divergence_audit's own
    # PASS/WARN/REJECT verdict — see divergence_audit.classify_divergence_
    # support for why this is a mapping, not a second independent judgment.
    # None whenever divergence_audit is None or never triggered.
    divergence_support: str | None = None

    # --- K1b: honest calibration status (additive) -----------------------
    # Always "UNCALIBRATED" today — see DEFAULT_CONFIDENCE_CALIBRATION_STATUS
    # above for why. Becomes a real per-market computed value once Phase N2
    # has resolved-shadow-forecast history to fit a calibration curve from.
    confidence_calibration_status: ConfidenceCalibrationStatus = DEFAULT_CONFIDENCE_CALIBRATION_STATUS

    # --- J2/K1: genuine multi-dimensional composites (additive) -----------
    # data_quality (legacy DataQualityBreakdown above, kept byte-for-byte
    # backward compatible for existing `.data_quality.total` consumers) is
    # now itself computed from these same real dimensions where the legacy
    # field shape has room for them; `data_quality_composite` is the full,
    # honest per-dimension breakdown additionally exposed here, including
    # dimensions the legacy 6-field shape had no slot for (evidence
    # relevance tiers, source independence, structured-data availability,
    # model agreement, provider health). See confidence.py for the formula.
    data_quality_composite: QualityComposite | None = None
    # --- I3: real historical comparable cases (additive) -------------------
    # The actual (question, similarity_score, outcome, weight_share) rows
    # that fed the history submodel's weighted baseline (Phase D's
    # find_comparable_cases / history.compute_weighted_baseline), top 10 by
    # similarity. Empty when the history submodel ran on the legacy
    # category-equality path (no per-case similarity score exists there) or
    # had zero usable comparable cases.
    historical_comparables: tuple[dict, ...] = field(default_factory=tuple)
    # Part 2/Part E (correctness-hardening round 2, additive): explicit
    # candidate accounting from history.WeightedBaselineResult so the UI/API
    # can show how many candidates were even considered vs how many actually
    # passed the compatibility gate (`_passes_compatibility_gate` in
    # history.py) and fed the weighted baseline above. 0/0/0 when the
    # history submodel didn't run on the weighted path at all (legacy
    # category-equality path, or zero candidates in the DB).
    historical_candidate_count: int = 0
    historical_accepted_count: int = 0
    historical_rejected_count: int = 0
    # confidence_score above is now fed by this same composite approach
    # (see confidence.compute_confidence) — confidence_composite is the
    # full per-dimension breakdown, proving confidence is a function of
    # measured data quality/robustness signals only, never of how far the
    # probability estimate sits from 50%.
    confidence_composite: QualityComposite | None = None

    # --- Data Gap Engine (Phase O, connected) ------------------------------
    # Real gap-detection output (data_gaps.py, calculated from this same
    # prediction run's own category/event_type/comparable-count/evidence-
    # relations values — see engine.py). None only when the gap calculation
    # itself could not run at all (it always can today; kept Optional for
    # forward-compatibility and so tests can distinguish "never computed"
    # from "computed, zero gaps found"). Diagnostic/explanatory only — never
    # feeds back into any probability field above.
    data_gaps: DataGapReport | None = None

    # --- Forecast Maturity (steering point 14, additive) -------------------
    # See ForecastMaturity literal above and prediction/maturity.py for the
    # classification function and documented thresholds. EXPERT_HEURISTIC
    # provenance (like PriorProvenance) — not fitted against resolved-
    # outcome history, which doesn't exist yet.
    forecast_maturity: ForecastMaturity = "NO_FORECAST"
    maturity_breakdown: tuple[dict, ...] = field(default_factory=tuple)

    # --- World State (steering point 9/21, additive) -----------------------
    # See prediction/world_state.py for the audit conclusion and exact
    # assembly logic. Assembled entirely from fields the engine already
    # computed elsewhere this run (MarketProposition.yes_condition/
    # no_condition/deadline, resolution_date-derived time remaining,
    # IndependentEvidenceResult's claim/counter-evidence counts) — never a
    # new probability-affecting signal. None only if the proposition itself
    # could not be parsed (should not happen in practice today).
    world_state: WorldState | None = None

    # --- ROUND-1 additive fields (Market Understanding / Resolution Engine)
    # `proposition`: the full parsed MarketProposition (semantics.py) for
    # this market — previously computed internally by engine.py but never
    # exposed on the result at all; a caller had no reachable field for the
    # new subject_type/domain/contract_type/resolution_mechanism/
    # semantic_confidence fields without this.
    proposition: MarketProposition | None = None
    # `resolution_semantics`: the Resolution Engine's structured output
    # (resolution_semantics.py) — measurement/threshold/required_source/
    # ambiguities/confidence. Diagnostic/explanatory only, like world_state;
    # never an input to any probability field (confidence.py consumes its
    # .confidence as one composite dimension among several, additively).
    resolution_semantics: ResolutionSemantics | None = None

    # --- Block D Part 4: Change Triggers (additive) -------------------------
    # Deterministic "what would change our assessment" statements, derived
    # only from real structured data already computed this run (Block C's
    # ResolutionPath open/blocked steps, Part 3's Data Gap Engine
    # critical/high gaps, claims.py's real contradiction count, and the
    # divergence-audit REJECT state) — see prediction/change_triggers.py.
    # No LLM call, no invented text. Empty tuple is the honest default for
    # the majority of markets with no concrete derivable trigger.
    change_triggers: tuple[str, ...] = field(default_factory=tuple)

    # --- Block E Part 1: Decision Engine (additive) -------------------------
    # See prediction/decision.py for the full weighting logic. `decision_state`
    # is the actionable recommendation-strength label; `decision_reasons` is
    # an itemized, honest explanation (never a fabricated confidence story).
    decision_state: DecisionState = "NO_POSITION"
    decision_reasons: tuple[str, ...] = field(default_factory=tuple)

    # --- Phase E: Structured World State (additive) -------------------------
    # The single compact per-market summary (structured_state.py) composed
    # from world_state/data_gaps already computed above -- CONFIRMED/
    # DISPUTED facts, current state, completed/open resolution steps,
    # blockers, open questions, data gaps. Diagnostic/explanatory only,
    # like world_state itself; never a new probability input. None only
    # when world_state could not be assembled at all.
    structured_world_state: StructuredWorldState | None = None

    # --- Phase F: Next Event Engine (additive) -------------------------------
    # The most likely next resolution-relevant event, derived purely from the
    # real ResolutionPath (next_event.py) -- never an LLM guess. None only
    # when structured_world_state itself could not be assembled; status
    # "UNKNOWN"/next_event_type None is the honest default for the majority
    # of markets with no known multi-step resolution template.
    next_event: NextEvent | None = None

    # --- Phase G: Event Clock (additive) --------------------------------------
    # Whether a possible future path can still happen in time. No fabricated
    # durations -- estimated_minimum/typical_path_time stay None when no real
    # per-step duration dataset exists (currently: always). path_feasibility
    # is only ever "IMPOSSIBLE" from a plain deadline-vs-now comparison, never
    # from a guessed duration. See event_clock.py.
    event_clock: EventClock | None = None

    # --- Phase H: Expected vs Observed (additive) -----------------------------
    # Whether the previously-expected step has actually been observed, and
    # whether the currently-expected one is running late against the
    # market's own real deadline. lateness_hours is only ever a real
    # deadline-vs-now delta, never a guessed per-step expected-by date. See
    # expected_vs_observed.py.
    expected_vs_observed: ExpectedVsObserved | None = None

    # --- Phase I: Conditional Transition Engine (additive) --------------------
    # One entry per remaining real resolution step, chained to its real
    # prerequisite. conditional_probability is honestly None for every
    # entry today -- no real per-step transition-rate dataset exists yet;
    # see conditional_transitions.py.
    conditional_transitions: tuple[ConditionalTransition, ...] = field(default_factory=tuple)

    # Phase J: explicit branch tree derived from the same resolution path.
    scenario_tree: ScenarioTree | None = None

    # Phase K: exact removals from the pre-news linear ensemble only.
    sensitivity_audit: SensitivityAudit | None = None

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_yes_probability": self.market_yes_probability,
            "market_no_probability": self.market_no_probability,
            "estimated_yes_probability": self.estimated_yes_probability,
            "estimated_no_probability": self.estimated_no_probability,
            "gross_yes_edge": self.gross_yes_edge,
            "net_yes_edge": self.net_yes_edge,
            "confidence_score": self.confidence_score,
            "data_quality_score": self.data_quality.total,
            "data_quality_breakdown": self.data_quality.as_dict(),
            "uncertainty_lower": self.uncertainty_lower,
            "uncertainty_upper": self.uncertainty_upper,
            "recommendation": self.recommendation,
            "comparable_sample_size": self.comparable_sample_size,
            "observed_historical_yes_rate": self.observed_historical_yes_rate,
            "reasoning_notes": list(self.reasoning_notes),
            "deadline_phase": self.deadline_phase,
            "submodel_estimates": [
                {
                    "name": s.name, "estimated_yes_probability": s.estimated_yes_probability,
                    "weight": s.weight, "available": s.available, "detail": s.detail,
                }
                for s in self.submodel_estimates
            ],
            "ensemble_agreement": self.ensemble_agreement,
            "scenarios": self.scenarios.as_dict() if self.scenarios else None,
            "news_sentiment_score": self.news_sentiment_score,
            "news_confirmation_count": self.news_confirmation_count,
            "independent_evidence": self.independent_evidence.as_dict() if self.independent_evidence else None,
            "resolution_edge": self.resolution_edge.as_dict() if self.resolution_edge else None,
            "cross_market": self.cross_market.as_dict() if self.cross_market else None,
            "reaction_lag": self.reaction_lag.as_dict() if self.reaction_lag else None,
            "orderbook_metrics": self.orderbook_metrics.as_dict() if self.orderbook_metrics else None,
            "trade_flow_metrics": self.trade_flow_metrics.as_dict() if self.trade_flow_metrics else None,
            "wallet_concentration": self.wallet_concentration.as_dict() if self.wallet_concentration else None,
            "market_reliability": self.market_reliability.as_dict() if self.market_reliability else None,
            "manipulation_risk": self.manipulation_risk.as_dict() if self.manipulation_risk else None,
            "event_relation_signals": [s.as_dict() for s in self.event_relation_signals],
            "independent_probability": self.independent_probability,
            "market_consensus_probability": self.market_consensus_probability,
            "blended_probability": self.blended_probability,
            "calibrated_probability": self.calibrated_probability,
            "market_probability": self.market_probability,
            "model_hypothesis_probability": self.model_hypothesis_probability,
            "evidence_backed_probability": self.evidence_backed_probability,
            "published_forecast_probability": self.published_forecast_probability,
            "forecast_status": self.forecast_status,
            "contribution_breakdown": [c.as_dict() for c in self.contribution_breakdown],
            "forecast_suppression_reason": self.forecast_suppression_reason,
            "confidence_calibration_status": self.confidence_calibration_status,
            "divergence_audit": self.divergence_audit.as_dict() if self.divergence_audit else None,
            "divergence_support": self.divergence_support,
            "data_quality_composite": self.data_quality_composite.as_dict() if self.data_quality_composite else None,
            "confidence_composite": self.confidence_composite.as_dict() if self.confidence_composite else None,
            "historical_comparables": list(self.historical_comparables),
            "historical_candidate_count": self.historical_candidate_count,
            "historical_accepted_count": self.historical_accepted_count,
            "historical_rejected_count": self.historical_rejected_count,
            "data_gaps": self.data_gaps.as_dict() if self.data_gaps else None,
            "forecast_maturity": self.forecast_maturity,
            "maturity_breakdown": list(self.maturity_breakdown),
            "world_state": self.world_state.as_dict() if self.world_state else None,
            "proposition": self.proposition.as_dict() if self.proposition else None,
            "resolution_semantics": self.resolution_semantics.as_dict() if self.resolution_semantics else None,
            "change_triggers": list(self.change_triggers),
            "decision_state": self.decision_state,
            "decision_reasons": list(self.decision_reasons),
            "structured_world_state": (
                self.structured_world_state.as_dict() if self.structured_world_state else None
            ),
            "next_event": self.next_event.as_dict() if self.next_event else None,
            "event_clock": self.event_clock.as_dict() if self.event_clock else None,
            "expected_vs_observed": (
                self.expected_vs_observed.as_dict() if self.expected_vs_observed else None
            ),
            "conditional_transitions": [t.as_dict() for t in self.conditional_transitions],
            "scenario_tree": self.scenario_tree.as_dict() if self.scenario_tree else None,
            "sensitivity_audit": self.sensitivity_audit.as_dict() if self.sensitivity_audit else None,
        }
