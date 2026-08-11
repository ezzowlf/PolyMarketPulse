"""Block E, Part 1: Decision Engine.

Answers a question distinct from ForecastMaturity ("how much should a
reader trust this forecast") and from Recommendation (a directional
YES/NO-shaped label already used elsewhere): "is there an actionable edge
here worth surfacing to a reader at all, and how strong is it?"

Hard, explicit rule (project owner's requirement, non-negotiable): a large
`model_hypothesis_probability` deviation from the market ALONE — with
`published_forecast_probability is None` — must NEVER produce anything
above NO_POSITION/WATCH. This is enforced structurally below: every branch
that can reach POSSIBLE_EDGE/STRONG_EDGE requires
`published_forecast_probability` to be a real, non-None number first.

Pure function of an already-computed PredictionResult. No new probability
math, no network calls, no LLM. EXPERT_HEURISTIC thresholds (like
maturity.py's), documented, not fitted against resolved-outcome history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import DecisionState

if TYPE_CHECKING:
    from .types import PredictionResult

# Below this |edge| in probability points, an edge is not worth flagging
# even when everything else about the forecast is solid.
MIN_EDGE_FOR_WATCH = 0.03
MIN_EDGE_FOR_POSSIBLE_EDGE = 0.06
MIN_EDGE_FOR_STRONG_EDGE = 0.12

MIN_CONFIDENCE_FOR_POSSIBLE_EDGE = 55.0
MIN_CONFIDENCE_FOR_STRONG_EDGE = 75.0

MATURITY_RANK = {
    "NO_FORECAST": 0,
    "CONTEXT_ONLY": 1,
    "HYPOTHESIS": 2,
    "PARTIAL_FORECAST": 3,
    "SUPPORTED_FORECAST": 4,
    "MATURE_FORECAST": 5,
}
MIN_MATURITY_FOR_POSSIBLE_EDGE = MATURITY_RANK["SUPPORTED_FORECAST"]
MIN_MATURITY_FOR_STRONG_EDGE = MATURITY_RANK["SUPPORTED_FORECAST"]

# Below this many hours to deadline, thin/unresolved evidence is penalized
# harder (less time for the market to correct or for evidence to firm up).
DEADLINE_CAUTION_HOURS = 12.0

# Liquidity/spread gates (market_snapshots real fields). A published edge
# in an illiquid or wide-spread market is real but not tradeable, so it is
# capped at POSSIBLE_EDGE even when everything else qualifies for STRONG_EDGE.
MIN_LIQUIDITY_FOR_STRONG_EDGE = 5_000.0
MAX_SPREAD_FOR_STRONG_EDGE = 0.05


def _published_edge(result: PredictionResult) -> float | None:
    if result.published_forecast_probability is None or result.market_probability is None:
        return None
    return result.published_forecast_probability - result.market_probability


def compute_decision_state(
    result: PredictionResult,
    liquidity: float | None = None,
    spread: float | None = None,
    deadline_hours: float | None = None,
) -> tuple[DecisionState, tuple[str, ...]]:
    """Returns (decision_state, decision_reasons). `liquidity`/`spread` are
    the same real `market_snapshots` fields opportunities.py already reads
    (passed in by the caller — this module does not touch storage).
    `deadline_hours` is likewise the same computation opportunities.py
    already performs."""
    reasons: list[str] = []

    # --- Hard rule: no published_forecast_probability -> never above WATCH,
    # no matter how large model_hypothesis_probability's deviation is. -----
    if result.published_forecast_probability is None:
        if result.model_hypothesis_probability is not None and result.market_probability is not None:
            raw_gap = abs(result.model_hypothesis_probability - result.market_probability)
            if raw_gap >= MIN_EDGE_FOR_WATCH:
                reasons.append(
                    f"model_hypothesis_probability diverges from market by {raw_gap:.1%}, but "
                    "published_forecast_probability is None (not evidence-backed/publishable) — "
                    "capped at WATCH per hard rule, never a real edge."
                )
                return "WATCH", tuple(reasons)
        reasons.append("published_forecast_probability is None — no real edge to consider.")
        return "NO_POSITION", tuple(reasons)

    edge = _published_edge(result)
    if edge is None:
        reasons.append("published_forecast_probability exists but market_probability is None — edge magnitude unknown.")
        return "WATCH", tuple(reasons)

    abs_edge = abs(edge)
    maturity_rank = MATURITY_RANK.get(result.forecast_maturity, 0)
    confidence = result.confidence_score
    divergence_verdict = result.divergence_audit.verdict if result.divergence_audit is not None else None

    reasons.append(f"published edge = {edge:+.1%} (published_forecast_probability - market_probability)")
    reasons.append(f"forecast_maturity={result.forecast_maturity}, confidence_score={confidence:.1f}")
    if divergence_verdict is not None:
        reasons.append(f"divergence_audit.verdict={divergence_verdict}")

    if divergence_verdict == "REJECT":
        reasons.append("divergence audit REJECTed — capped at NO_POSITION regardless of edge.")
        return "NO_POSITION", tuple(reasons)

    if abs_edge < MIN_EDGE_FOR_WATCH:
        reasons.append(f"edge {abs_edge:.1%} below {MIN_EDGE_FOR_WATCH:.0%} WATCH floor.")
        return "NO_POSITION", tuple(reasons)

    if deadline_hours is not None and deadline_hours < DEADLINE_CAUTION_HOURS and maturity_rank < MIN_MATURITY_FOR_POSSIBLE_EDGE:
        reasons.append(f"deadline in {deadline_hours:.1f}h with maturity below SUPPORTED_FORECAST — capped at WATCH.")
        return "WATCH", tuple(reasons)

    if abs_edge < MIN_EDGE_FOR_POSSIBLE_EDGE or maturity_rank < MIN_MATURITY_FOR_POSSIBLE_EDGE or confidence < MIN_CONFIDENCE_FOR_POSSIBLE_EDGE:
        reasons.append("edge/maturity/confidence below POSSIBLE_EDGE thresholds.")
        return "WATCH", tuple(reasons)

    # --- POSSIBLE_EDGE qualifies here. Check STRONG_EDGE upgrade. ---------
    illiquid = liquidity is not None and liquidity < MIN_LIQUIDITY_FOR_STRONG_EDGE
    wide_spread = spread is not None and spread > MAX_SPREAD_FOR_STRONG_EDGE
    if illiquid or wide_spread:
        reasons.append(
            f"liquidity={liquidity}, spread={spread} — capped at POSSIBLE_EDGE (illiquid/wide-spread market, "
            "real edge but not cleanly tradeable)."
        )
        return "POSSIBLE_EDGE", tuple(reasons)

    if (
        abs_edge >= MIN_EDGE_FOR_STRONG_EDGE
        and maturity_rank >= MIN_MATURITY_FOR_STRONG_EDGE
        and confidence >= MIN_CONFIDENCE_FOR_STRONG_EDGE
        and divergence_verdict != "WARN"
    ):
        reasons.append("edge/maturity/confidence/divergence all clear STRONG_EDGE thresholds.")
        return "STRONG_EDGE", tuple(reasons)

    reasons.append("qualifies for POSSIBLE_EDGE; does not clear all STRONG_EDGE thresholds.")
    return "POSSIBLE_EDGE", tuple(reasons)
