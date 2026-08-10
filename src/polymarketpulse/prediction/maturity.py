"""Forecast Maturity classification (steering point 14).

A single, coarse label answering "how much should a reader trust THIS
specific forecast" — built entirely from signals the engine already
computes (forecast_status, the K1/J2 confidence/data-quality composites,
the real evidence-tier mix behind independent_evidence, the divergence
red-team audit's verdict, and the Data Gap Engine's severity counts). It
invents no new probability-affecting signal; it only reads and buckets
values PredictionResult already carries.

PriorProvenance-style honesty note: the thresholds below are
EXPERT_HEURISTIC, exactly like base_rates.py's manually-authored table and
K3's PriorProvenance tagging elsewhere in this codebase — reasoned and
documented, but NOT fitted against resolved-outcome history, because no
real out-of-sample resolved-forecast dataset exists yet (that is Phase N2's
job). Treat the cutoffs as a defensible starting point, not a calibrated
model. Revisit once real Brier/reliability data exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import ForecastMaturity

if TYPE_CHECKING:
    from .types import PredictionResult


def _direct_tier_evidence_count(result: PredictionResult) -> int:
    """How many independent-evidence items are DIRECT_YES/DIRECT_NO tier
    (semantics.classify_evidence_relation's strongest, on-topic-and-
    explicit tier) — the closest thing this codebase has to "hard
    evidence" vs. "weak/contextual evidence" for a market."""
    if result.independent_evidence is None:
        return 0
    all_items = (*result.independent_evidence.evidence_for_yes, *result.independent_evidence.evidence_for_no)
    return sum(1 for e in all_items if e.relation_label in ("DIRECT_YES", "DIRECT_NO"))


def _weak_or_context_only(result: PredictionResult) -> bool:
    """True when independent evidence exists but none of it clears WEAK/
    CONTEXT tier — i.e. there is *something* on the page, but nothing an
    honest reader would call real evidence for the specific proposition."""
    if result.independent_evidence is None or not result.independent_evidence.available:
        return False
    all_items = (*result.independent_evidence.evidence_for_yes, *result.independent_evidence.evidence_for_no)
    if not all_items:
        return False
    strong_tiers = ("DIRECT_YES", "DIRECT_NO", "SUPPORTS_YES", "SUPPORTS_NO")
    return not any(e.relation_label in strong_tiers for e in all_items)


def _has_live_structured_domain_path(result: PredictionResult) -> bool:
    """True only for a production model backed by state fetched this run."""
    available = {
        s.name for s in result.submodel_estimates
        if s.available and s.estimated_yes_probability is not None
    }
    if not available.intersection({"macro", "quant"}) or result.world_state is None:
        return False
    sources = {v.source for v in result.world_state.state_variables}
    return bool(("macro" in available and "fred" in sources) or ("quant" in available and "coingecko" in sources))


def build_maturity_breakdown(result: PredictionResult) -> tuple[dict, ...]:
    """Itemized, domain-aware explanation of the maturity decision."""
    proposition = result.proposition
    resolution = result.resolution_semantics
    structured = _has_live_structured_domain_path(result)
    domain = proposition.domain if proposition else None
    direct = _direct_tier_evidence_count(result)
    evidence_items = ()
    if result.independent_evidence is not None:
        evidence_items = (
            *result.independent_evidence.evidence_for_yes,
            *result.independent_evidence.evidence_for_no,
        )
    relevant_evidence = sum(
        item.relation_label in ("DIRECT_YES", "DIRECT_NO", "SUPPORTS_YES", "SUPPORTS_NO")
        for item in evidence_items
    )
    gaps = result.data_gaps
    high_critical = 0 if gaps is None else gaps.high_gaps + gaps.critical_gaps
    verdict = result.divergence_audit.verdict if result.divergence_audit else None
    path = result.world_state.path_to_resolution if result.world_state else None
    path_known = bool(path and path.current_state != "UNKNOWN")

    def row(name: str, status: str, reason: str) -> dict:
        return {"dimension": name, "status": status, "reason": reason}

    evidence_status = "N/A" if structured and domain in ("MACRO", "CRYPTO") else (
        "PASS" if relevant_evidence else "FAIL"
    )
    path_status = "N/A" if domain not in ("POLITICS", "GEOPOLITICS") else (
        "PASS" if path_known else "FAIL"
    )
    sources = {v.source for v in result.world_state.state_variables} if result.world_state else set()
    return (
        row("SEMANTICS", "PASS" if proposition and proposition.proposition_status == "CLEAR" else "FAIL",
            f"proposition_status={proposition.proposition_status if proposition else 'missing'}"),
        row("RESOLUTION", "PASS" if resolution and resolution.confidence >= 0.7 else "FAIL",
            f"resolution confidence={resolution.confidence if resolution else None}"),
        row("WORLD_STATE", "PASS" if structured or path_known else "FAIL",
            f"structured sources={sorted(sources)}; path state known={path_known}"),
        row("DOMAIN_MODEL", "PASS" if any(s.available and s.name in {"macro", "quant", "politics", "geopolitics", "sports"} for s in result.submodel_estimates) else "FAIL",
            "At least one eligible specialized model produced a numeric estimate."),
        row("LIVE_PROVIDER", "PASS" if structured else ("N/A" if domain not in ("MACRO", "CRYPTO") else "FAIL"),
            f"live structured provider sources={sorted(sources)}"),
        row("EVIDENCE", evidence_status,
            f"direct={direct}, relevant direct/support={relevant_evidence}; optional for live structured macro/quant"),
        row("PATH_TO_RESOLUTION", path_status,
            f"domain={domain}, current path state={path.current_state if path else None}"),
        row("UNCERTAINTY", "PASS" if result.confidence_score >= 70 else "FAIL",
            f"confidence={result.confidence_score:.1f}/100"),
        row("DATA_QUALITY", "PASS" if result.data_quality_composite and result.data_quality_composite.score >= 60 else "FAIL",
            f"data quality={result.data_quality_composite.score if result.data_quality_composite else None}"),
        row("BLOCKING_GAPS", "PASS" if high_critical == 0 else "FAIL",
            f"high+critical gaps={high_critical}"),
        row("DIVERGENCE", "PASS" if verdict != "REJECT" else "FAIL",
            f"verdict={verdict}"),
    )


def classify_forecast_maturity(result: PredictionResult) -> ForecastMaturity:
    """Classify a completed PredictionResult into the Forecast Maturity
    taxonomy. Pure function of already-computed fields — never recomputes
    or second-guesses the probability itself.

    Exact rules (evaluated top to bottom, first match wins):

    1. NO_FORECAST
       independent_probability is None, or the divergence red-team audit
       rejected publication (`forecast_status=FORECAST_SUPPRESSED`). The
       underlying market-blind estimate remains observable in the latter
       case, but cannot mature into an opportunity.

    2. CONTEXT_ONLY
       An independent_probability exists but the evidence behind it is
       thin in a specific way: no DIRECT-tier evidence AND (evidence that
       exists is entirely WEAK/CONTEXT tier, OR the only real support is
       a single historical comparable case (comparable_sample_size <= 1)
       with no independent evidence at all). This is "we have a page, not
       a forecast" — a single weak data point dressed up as a number.

    3. HYPOTHESIS
       A real but low-confidence independent estimate: confidence_score
       < 40, or fewer than 3 historical comparables AND no DIRECT-tier
       evidence. A genuine estimate exists, but it would be misleading to
       call it more than a hypothesis.

    4. PARTIAL_FORECAST
       Moderate evidence/confidence (confidence_score 40..70, or DIRECT
       evidence present but data_quality is mediocre) while REAL,
       unresolved data gaps remain (data_gaps.critical_gaps > 0 or
       data_gaps.high_gaps > 0). The forecast is real but visibly
       incomplete — exactly the case the Data Gap Engine exists to
       surface.

    5. SUPPORTED_FORECAST
       Solid evidence + confidence + data quality (confidence_score >= 70
       and data_quality_composite.score >= 60) with no HIGH/CRITICAL data
       gaps remaining, and the divergence audit (if it ran at all) did not
       REJECT.

    6. MATURE_FORECAST
       The strongest real case: confidence_score >= 85 AND
       data_quality_composite.score >= 75 AND (at least one DIRECT-tier
       evidence item OR comparable_sample_size >= 20) AND zero data gaps
       of any severity (data_gaps.total_gaps == 0, or data_gaps is None
       meaning the calculation itself found nothing to flag) AND the
       divergence audit verdict is PASS whenever it was triggered at all.
       Expected to be rare/hard to reach with the data currently available
       locally — see maturity acceptance notes in HANDOFF.md; that is
       reported honestly rather than loosened to make the bucket non-empty.
    """
    # --- 1. NO_FORECAST ---------------------------------------------------
    if result.independent_probability is None or result.forecast_status == "FORECAST_SUPPRESSED":
        return "NO_FORECAST"

    confidence = result.confidence_score
    dq_score = result.data_quality_composite.score if result.data_quality_composite is not None else None
    direct_count = _direct_tier_evidence_count(result)
    comparable_n = result.comparable_sample_size

    gaps = result.data_gaps
    critical_gaps = gaps.critical_gaps if gaps is not None else 0
    high_gaps = gaps.high_gaps if gaps is not None else 0
    total_gaps = gaps.total_gaps if gaps is not None else 0

    divergence_verdict = result.divergence_audit.verdict if result.divergence_audit is not None else None

    # --- 2. CONTEXT_ONLY ---------------------------------------------------
    structured_domain_path = _has_live_structured_domain_path(result)
    thin_single_case = not structured_domain_path and comparable_n <= 1 and (
        result.independent_evidence is None or not result.independent_evidence.available
    )
    if direct_count == 0 and (_weak_or_context_only(result) or thin_single_case):
        return "CONTEXT_ONLY"

    # --- 3. HYPOTHESIS ------------------------------------------------------
    if confidence < 40 or (not structured_domain_path and comparable_n < 3 and direct_count == 0):
        return "HYPOTHESIS"

    # --- 6. MATURE_FORECAST (checked before 4/5 since it is a strict
    # superset of SUPPORTED_FORECAST's conditions) -------------------------
    if (
        confidence >= 85
        and dq_score is not None and dq_score >= 75
        and (direct_count >= 1 or comparable_n >= 20)
        and total_gaps == 0
        and divergence_verdict in (None, "PASS")
    ):
        return "MATURE_FORECAST"

    # --- 5. SUPPORTED_FORECAST ----------------------------------------------
    if (
        confidence >= 70
        and dq_score is not None and dq_score >= 60
        and critical_gaps == 0 and high_gaps == 0
        and divergence_verdict != "REJECT"
    ):
        return "SUPPORTED_FORECAST"

    # --- 4. PARTIAL_FORECAST (everything else with a real, non-thin,
    # non-hypothesis-tier estimate) ------------------------------------------
    return "PARTIAL_FORECAST"
