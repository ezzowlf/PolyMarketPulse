"""Confidence score — separate from the probability estimate itself. A
model can be very sure the coin is fair (high confidence, p=0.5) or very
unsure about a lopsided-looking market (low confidence, p=0.8). Conflating
the two is exactly the mistake the whole Phase-7/V2 architecture exists to
prevent (see ai/prompts.py rule 7: "a score of 80 is never automatically an
80% probability").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .divergence_audit import compute_model_disagreement
from .types import DataQualityBreakdown, QualityComposite, QualityDimension, SubmodelEstimate

if TYPE_CHECKING:
    from .evidence import IndependentEvidenceResult
    from .history import WeightedBaselineResult
    from .semantics import MarketProposition

# --- J2/K1: shared real-signal dimension helpers ---------------------------
# These are used to build BOTH the J2 data_quality composite and the K1
# confidence composite. Each returns a QualityDimension: `available=False`
# means the signal is genuinely not computable today (not a fabricated
# mid-value) — composite builders below exclude unavailable dimensions from
# the weighted average and redistribute their weight, rather than silently
# scoring them as flattering defaults.

# Evidence relation tiers (semantics.classify_evidence_relation labels) ->
# a real relevance score. DIRECT entailment/contradiction is worth full
# marks; SUPPORTS is meaningfully weaker; WEAK (tone-only, gated) weaker
# still; CONTEXT/IRRELEVANT/AMBIGUOUS score at or near zero — this is the
# direct fix for "12 irrelevant articles inflate quality": a pile of
# CONTEXT/IRRELEVANT items no longer buys a high score just by existing.
_RELATION_TIER_SCORE: dict[str, float] = {
    "DIRECT_YES": 100.0, "DIRECT_NO": 100.0,
    "SUPPORTS_YES": 65.0, "SUPPORTS_NO": 65.0,
    "WEAK_YES": 30.0, "WEAK_NO": 30.0,
    "CONTEXT": 8.0, "IRRELEVANT": 0.0, "AMBIGUOUS": 12.0,
}


def _proposition_clarity_dimension(proposition: MarketProposition | None) -> QualityDimension:
    if proposition is None:
        return QualityDimension(
            name="proposition_clarity", raw_value=None, normalized_score=None, available=False,
            reason="No parsed proposition supplied for this call.",
        )
    n_flags = len(proposition.ambiguity_flags)
    if proposition.proposition_status == "CLEAR":
        score = 100.0 if n_flags == 0 else max(70.0, 100.0 - 10.0 * n_flags)
    else:
        score = max(10.0, 55.0 - 15.0 * n_flags)
    return QualityDimension(
        name="proposition_clarity", raw_value=float(n_flags), normalized_score=round(score, 1), available=True,
        reason=f"proposition_status={proposition.proposition_status}, {n_flags} ambiguity flag(s).",
    )


def _historical_coverage_dimension(
    comparable_sample_size: int, history_uncertainty: WeightedBaselineResult | None,
) -> QualityDimension:
    if history_uncertainty is not None and history_uncertainty.effective_sample_size > 0:
        ess = history_uncertainty.effective_sample_size
        score = min(100.0, (ess / 30.0) * 100.0)
        return QualityDimension(
            name="historical_coverage", raw_value=ess, normalized_score=round(score, 1), available=True,
            reason=f"Kish effective sample size (ESS)={ess:.2f} across weighted comparable cases.",
        )
    if comparable_sample_size > 0:
        # Legacy category-equality path: no ESS concept exists there (see
        # history._compute_history_estimate_legacy) — report honestly with
        # the cruder raw case count rather than pretending we have an ESS.
        score = min(100.0, comparable_sample_size * 8.0)
        return QualityDimension(
            name="historical_coverage", raw_value=float(comparable_sample_size), normalized_score=round(score, 1),
            available=True, reason=f"{comparable_sample_size} comparable case(s) (legacy path, no ESS available).",
        )
    return QualityDimension(
        name="historical_coverage", raw_value=0.0, normalized_score=None, available=False,
        reason="Zero comparable historical cases found.",
    )


def _evidence_relevance_dimension(independent_evidence: IndependentEvidenceResult | None) -> QualityDimension:
    if independent_evidence is None:
        return QualityDimension(
            name="evidence_relevance", raw_value=None, normalized_score=None, available=False,
            reason="No independent_evidence result supplied.",
        )
    items = (*independent_evidence.evidence_for_yes, *independent_evidence.evidence_for_no)
    if not items:
        return QualityDimension(
            name="evidence_relevance", raw_value=0.0, normalized_score=None, available=False,
            reason="No linked evidence items to score relevance tiers over.",
        )
    tier_scores = [_RELATION_TIER_SCORE.get(e.relation_label, 12.0) for e in items]
    avg = sum(tier_scores) / len(tier_scores)
    n_direct = sum(1 for e in items if e.relation_label.startswith("DIRECT"))
    return QualityDimension(
        name="evidence_relevance", raw_value=avg, normalized_score=round(avg, 1), available=True,
        reason=f"{len(items)} evidence item(s) scored by relation tier ({n_direct} DIRECT-tier).",
    )


def _source_quality_independence_dimension(
    independent_evidence: IndependentEvidenceResult | None,
) -> QualityDimension:
    if independent_evidence is None or not independent_evidence.available:
        return QualityDimension(
            name="source_quality_independence", raw_value=None, normalized_score=None, available=False,
            reason="No available independent evidence to derive source quality/independence from.",
        )
    items = (*independent_evidence.evidence_for_yes, *independent_evidence.evidence_for_no)
    if not items or independent_evidence.source_quality_score is None:
        return QualityDimension(
            name="source_quality_independence", raw_value=None, normalized_score=None, available=False,
            reason="No source_quality_score / evidence items available.",
        )
    distinct_domains = len({e.source_domain or e.source for e in items})
    # 3+ distinct source domains treated as fully independent — a genuine,
    # if simple, independence signal (distinct domains among the evidence
    # that actually fed this market, not merely a count of articles).
    independence_score = min(100.0, distinct_domains * 33.3)
    quality = independent_evidence.source_quality_score
    combined = quality * 0.5 + independence_score * 0.5
    return QualityDimension(
        name="source_quality_independence", raw_value=quality, normalized_score=round(combined, 1), available=True,
        reason=(
            f"source_quality_score={quality:.1f}, {distinct_domains} distinct source domain(s) "
            f"across {len(items)} evidence item(s)."
        ),
    )


def _structured_data_availability_dimension(
    specialized_estimates: list[SubmodelEstimate], eligible_models: tuple[str, ...],
) -> QualityDimension:
    if not eligible_models:
        return QualityDimension(
            name="structured_data_availability", raw_value=None, normalized_score=None, available=False,
            reason="No specialized model is eligible for this market's event_type/category (N/A, not a gap).",
        )
    from .specialized_router import (
        SPECIALIZED_MODEL_RELIABILITY,
        SPECIALIZED_MODEL_RELIABILITY_SCORE,
    )

    available_estimates = {s.name: s for s in specialized_estimates if s.available}
    if not available_estimates:
        return QualityDimension(
            name="structured_data_availability", raw_value=0.0, normalized_score=15.0, available=True,
            reason=(
                f"Eligible specialized model(s) {list(eligible_models)} produced no usable structured data "
                "this call — fell back to unavailable."
            ),
        )
    scores = [
        SPECIALIZED_MODEL_RELIABILITY_SCORE[SPECIALIZED_MODEL_RELIABILITY[name]] * 100.0
        for name in available_estimates
    ]
    avg = sum(scores) / len(scores)
    return QualityDimension(
        name="structured_data_availability", raw_value=avg, normalized_score=round(avg, 1), available=True,
        reason=(
            f"Structured data actually available from: {list(available_estimates)} "
            f"(reliability tags: {[SPECIALIZED_MODEL_RELIABILITY[n] for n in available_estimates]})."
        ),
    )


def _model_agreement_dimension(submodel_estimates: list[SubmodelEstimate]) -> QualityDimension:
    stdev = compute_model_disagreement(submodel_estimates)
    if stdev is None:
        return QualityDimension(
            name="model_agreement", raw_value=None, normalized_score=None, available=False,
            reason="Fewer than 2 available submodels to compare — agreement is not a meaningful concept here.",
        )
    score = max(0.0, 100.0 * (1 - min(1.0, stdev / 0.5)))
    return QualityDimension(
        name="model_agreement", raw_value=round(stdev, 4), normalized_score=round(score, 1), available=True,
        reason=f"Stdev across available submodel estimates={stdev:.3f} (reused from divergence_audit).",
    )


def _provider_health_dimension() -> QualityDimension:
    # Honest gap: no provider_health/circuit_breaker/success-failure
    # tracking exists anywhere in providers/ or news/ as of this pass (see
    # J2/K1 task notes). Building a real one requires wiring call-result
    # tracking into every external client (coingecko.py, rss.py, gdelt.py,
    # etc.) — a moderate lift with real regression risk to the 647+ test
    # baseline, judged out of scope for this pass. Reported honestly as
    # UNKNOWN/unavailable rather than fabricated.
    return QualityDimension(
        name="provider_health", raw_value=None, normalized_score=None, available=False,
        reason="No provider-health/circuit-breaker tracking exists in this codebase yet (honest gap).",
    )


def _freshness_dimension(aktualitaet: float) -> QualityDimension:
    return QualityDimension(
        name="freshness", raw_value=aktualitaet, normalized_score=aktualitaet, available=True,
        reason="Real timestamp-derived freshness score (J1), see compute_freshness_score.",
    )


def _uncertainty_width_dimension(history_uncertainty: WeightedBaselineResult | None) -> QualityDimension:
    if history_uncertainty is None or history_uncertainty.uncertainty_width is None:
        return QualityDimension(
            name="uncertainty_width", raw_value=None, normalized_score=None, available=False,
            reason="No Wilson-interval uncertainty width available (history submodel not on the weighted path).",
        )
    width = history_uncertainty.uncertainty_width
    score = max(0.0, 100.0 * (1 - min(1.0, width / 1.0)))
    return QualityDimension(
        name="uncertainty_width", raw_value=width, normalized_score=round(score, 1), available=True,
        reason=f"95% Wilson-interval width={width:.2%} (narrower is more confidence-worthy).",
    )


def _specialized_model_reliability_dimension(
    specialized_estimates: list[SubmodelEstimate],
) -> QualityDimension:
    available = [s for s in specialized_estimates if s.available]
    if not available:
        return QualityDimension(
            name="specialized_model_reliability", raw_value=None, normalized_score=None, available=False,
            reason="No specialized (Phase E) model contributed to this market's forecast.",
        )
    from .specialized_router import (
        SPECIALIZED_MODEL_RELIABILITY,
        SPECIALIZED_MODEL_RELIABILITY_SCORE,
    )

    tags = [SPECIALIZED_MODEL_RELIABILITY.get(s.name, "UNAVAILABLE") for s in available]
    scores = [SPECIALIZED_MODEL_RELIABILITY_SCORE[t] * 100.0 for t in tags]
    avg = sum(scores) / len(scores)
    return QualityDimension(
        name="specialized_model_reliability", raw_value=avg, normalized_score=round(avg, 1), available=True,
        reason=f"Contributing specialized model(s) {[s.name for s in available]} tagged {tags}.",
    )


def _legacy_signal_dimension(dq: DataQualityBreakdown) -> QualityDimension:
    """Folds the pre-K1 `DataQualityBreakdown` (liquidity, caller-supplied
    news_agreement/data_quality_report_score, resolution_rules_present) in
    as ONE modestly-weighted confidence dimension, alongside — not instead
    of — the new genuine dimensions above. These are real, already-computed
    signals (not fabricated), but several of them (news_agreement,
    data_quality_report_score) are caller-supplied scalars rather than
    signals this module derived itself from verifiable evidence, so they
    are deliberately kept a minority contributor rather than the dominant
    one they were pre-K1."""
    return QualityDimension(
        name="legacy_signals", raw_value=dq.total, normalized_score=dq.total, available=True,
        reason="Liquidity/resolution-clarity/caller-supplied data-quality signals (pre-K1 6-field average).",
    )


def _weighted_composite(dimensions: list[tuple[QualityDimension, float]], label: str) -> QualityComposite:
    """Weighted average over AVAILABLE dimensions only — unavailable
    dimensions' weight is proportionally redistributed among the available
    ones rather than scored as a flattering default. If every dimension is
    unavailable, the composite honestly reports a neutral 50.0 (documented
    "no signal" fallback, never a fabricated high number)."""
    dims = [d for d, _ in dimensions]
    available_weight = sum(w for d, w in dimensions if d.available and d.normalized_score is not None)
    if available_weight <= 0:
        return QualityComposite(
            dimensions=tuple(dims), score=50.0,
            formula_detail=f"{label}: no dimension was computable this call — neutral 50.0 fallback.",
        )
    score = sum(
        d.normalized_score * (w / available_weight) for d, w in dimensions if d.available and d.normalized_score is not None
    )
    n_avail = sum(1 for d in dims if d.available)
    return QualityComposite(
        dimensions=tuple(dims), score=round(min(100.0, max(0.0, score)), 1),
        formula_detail=(
            f"{label}: weighted average over {n_avail}/{len(dims)} available dimension(s), "
            "unavailable dimensions' weight redistributed proportionally among the rest."
        ),
    )

# --- J1: real freshness (Aktualität) computation --------------------------
# Prior to this fix, engine.py hardcoded `aktualitaet=85.0` unconditionally
# (see the removed KNOWN LIMITATION comment there) — a fixed value regardless
# of how stale the underlying evidence/price data actually was. This
# replaces it with a real, source-type-aware decay computed from actual
# timestamps: independently-sourced news/evidence uses each item's own
# recency_weight (already computed honestly in evidence.py from
# published_at vs now, 24h half-life there), and price/quant data uses the
# most recent market_snapshots.captured_at with a *much* shorter half-life
# (6h) because a stale price snapshot goes stale far faster than a news
# article's topical relevance does. When neither timestamped signal is
# available at all, this reports a neutral 50.0 (never a flattering
# fixed number) — an honest "we don't actually know how fresh this is",
# not a fabricated high score.
_PRICE_FRESHNESS_HALF_LIFE_HOURS = 6.0
_NO_TIMESTAMP_FALLBACK = 50.0


def compute_freshness_score(
    evidence_recency_weights: list[float],
    latest_price_captured_at: str | None,
    now: datetime | None = None,
    price_signal_is_primary: bool = False,
) -> tuple[float, str]:
    """Returns (aktualitaet 0..100, detail). `evidence_recency_weights` is the
    list of individual EvidenceFactor.recency_weight values (0..1, already
    decayed from real published_at timestamps) for whatever evidence fed
    this market's independent_evidence submodel. `latest_price_captured_at`
    is the most recent market_snapshots.captured_at ISO timestamp, or None
    if no price history exists. `price_signal_is_primary` should be True for
    quant/price-threshold markets, where price freshness matters more than
    news freshness."""
    now = now or datetime.now(UTC)
    signals: list[tuple[float, float]] = []  # (score_0_100, weight)

    if evidence_recency_weights:
        avg_recency = sum(evidence_recency_weights) / len(evidence_recency_weights)
        signals.append((avg_recency * 100, 0.4 if price_signal_is_primary else 0.7))

    if latest_price_captured_at:
        try:
            captured = datetime.fromisoformat(latest_price_captured_at)
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=UTC)
            hours_ago = max(0.0, (now - captured).total_seconds() / 3600)
            price_recency = 0.5 ** (hours_ago / _PRICE_FRESHNESS_HALF_LIFE_HOURS)
            signals.append((price_recency * 100, 0.6 if price_signal_is_primary else 0.3))
        except (ValueError, TypeError):
            pass

    if not signals:
        return (
            _NO_TIMESTAMP_FALLBACK,
            (
                "Aktualität: keine echten Zeitstempel (weder Evidenz noch Preis-Snapshot) verfügbar — "
                "neutraler Fallback, kein hartkodierter Wert."
            ),
        )

    total_weight = sum(w for _, w in signals)
    score = sum(v * w for v, w in signals) / total_weight
    return (
        round(score, 1),
        f"Aktualität aus {len(signals)} echten Zeitstempel-Signal(en) berechnet (Score={score:.1f}).",
    )


def compute_data_quality_composite(
    proposition: MarketProposition | None,
    history_uncertainty: WeightedBaselineResult | None,
    comparable_sample_size: int,
    independent_evidence: IndependentEvidenceResult | None,
    specialized_estimates: list[SubmodelEstimate],
    eligible_specialized_models: tuple[str, ...],
    aktualitaet: float,
) -> QualityComposite:
    """J2: the genuine data_quality composite. Weights (documented, not
    fitted — no resolved-forecast history yet to fit against, same honesty
    constraint as everything else in this module):

      proposition_clarity            0.15  — CLEAR vs AMBIGUOUS + flag count
      historical_coverage            0.20  — Kish ESS (or legacy case count)
      evidence_relevance             0.20  — relation-tier scored, not just
                                              "evidence exists"
      source_quality_independence    0.20  — trust table + distinct domains
      structured_data_availability   0.15  — did an eligible specialized
                                              model actually get real data
      freshness                      0.10  — J1's real timestamp decay

    provider_health is intentionally NOT weighted in (always UNKNOWN today,
    see _provider_health_dimension) but is still reported in the breakdown
    for transparency. model_agreement is a K1-confidence-specific dimension,
    not part of data_quality (agreement is about the estimate's robustness,
    not about how much/good data exists) — kept out of J2 deliberately."""
    dims: list[tuple[QualityDimension, float]] = [
        (_proposition_clarity_dimension(proposition), 0.15),
        (_historical_coverage_dimension(comparable_sample_size, history_uncertainty), 0.20),
        (_evidence_relevance_dimension(independent_evidence), 0.20),
        (_source_quality_independence_dimension(independent_evidence), 0.20),
        (_structured_data_availability_dimension(specialized_estimates, eligible_specialized_models), 0.15),
        (_freshness_dimension(aktualitaet), 0.10),
    ]
    composite = _weighted_composite(dims, "data_quality (J2)")
    # provider_health reported for transparency only, not weighted (see above).
    composite = QualityComposite(
        dimensions=composite.dimensions + (_provider_health_dimension(),),
        score=composite.score, formula_detail=composite.formula_detail,
    )
    return composite


def compute_confidence_composite(
    proposition: MarketProposition | None,
    history_uncertainty: WeightedBaselineResult | None,
    comparable_sample_size: int,
    independent_evidence: IndependentEvidenceResult | None,
    specialized_estimates: list[SubmodelEstimate],
    all_submodel_estimates: list[SubmodelEstimate],
    aktualitaet: float,
    deadline_phase_known: bool,
    legacy_data_quality: DataQualityBreakdown | None = None,
) -> QualityComposite:
    """K1: the genuine confidence composite — deliberately built from ONLY
    data-robustness/quality signals, never from the probability estimate's
    magnitude or its distance from 50%, so a low-probability, well-evidenced
    forecast can score HIGH confidence (see tests/test_confidence_composite
    for the explicit invariant test). Weights (documented, not fitted):

      effective_sample_size (ESS)        0.15
      uncertainty_width (Wilson, K2)     0.15
      evidence_strength_independence     0.15  — relation tiers + distinct
                                                  source domains
      source_quality                     0.10
      freshness                          0.10
      model_agreement                    0.15  — reused from
                                                  divergence_audit.compute_model_disagreement
      proposition_clarity                0.05
      specialized_model_reliability      0.10  — PRODUCTION_DATA_PATH /
                                                  FUNCTIONAL_BUT_UNCALIBRATED /
                                                  STRUCTURAL_SCAFFOLD tagging
      legacy_signals                     0.10  — liquidity/resolution-clarity/
                                                  caller-supplied data-quality
                                                  proxy (pre-K1 formula), kept
                                                  as a minority contributor —
                                                  see _legacy_signal_dimension.
      provider_health                    0.05  — always UNKNOWN today,
                                                  reported not weighted

    A small deadline_phase_known penalty is still applied afterward (kept
    from the pre-K1 formula) — it's a real "we don't know when this
    resolves" signal, unrelated to probability."""
    ess_dim = _historical_coverage_dimension(comparable_sample_size, history_uncertainty)
    ess_dim = QualityDimension(
        name="effective_sample_size", raw_value=ess_dim.raw_value, normalized_score=ess_dim.normalized_score,
        available=ess_dim.available, reason=ess_dim.reason,
    )
    dims: list[tuple[QualityDimension, float]] = [
        (ess_dim, 0.15),
        (_uncertainty_width_dimension(history_uncertainty), 0.15),
        (_evidence_relevance_dimension(independent_evidence), 0.15),
        (_source_quality_independence_dimension(independent_evidence), 0.10),
        (_freshness_dimension(aktualitaet), 0.10),
        (_model_agreement_dimension(all_submodel_estimates), 0.15),
        (_proposition_clarity_dimension(proposition), 0.05),
        (_specialized_model_reliability_dimension(specialized_estimates), 0.10),
    ]
    if legacy_data_quality is not None:
        dims.append((_legacy_signal_dimension(legacy_data_quality), 0.10))
    composite = _weighted_composite(dims, "confidence (K1)")
    composite = QualityComposite(
        dimensions=composite.dimensions + (_provider_health_dimension(),),
        score=composite.score, formula_detail=composite.formula_detail,
    )
    if not deadline_phase_known:
        composite = QualityComposite(
            dimensions=composite.dimensions, score=round(composite.score * 0.9, 1),
            formula_detail=composite.formula_detail + " Small penalty applied: resolution deadline unknown.",
        )
    return composite


def compute_confidence(
    data_quality: DataQualityBreakdown,
    submodel_estimates: list[SubmodelEstimate],
    market_stability: float,  # 0..1, e.g. 1 - normalized volatility
    deadline_phase_known: bool,
) -> tuple[float, float | None]:
    """Returns (confidence_score 0..100, ensemble_agreement 0..1 or None).

    Components:
    - data quality (as before, 0-100, weighted 35%)
    - number of *available* submodels contributing (more independent
      signals agreeing = more trustworthy), weighted 25%
    - ensemble agreement: how close the available submodels' estimates are
      to each other (low spread = high agreement), weighted 25%
    - market stability (calmer recent price action = more trustworthy
      snapshot), weighted 15%
    """
    available = [s for s in submodel_estimates if s.available and s.estimated_yes_probability is not None]
    n_available = len(available)
    coverage_score = min(100.0, n_available * 25.0)  # 4 submodels -> 100

    agreement: float | None = None
    agreement_score = 50.0  # neutral default when we can't measure agreement
    if n_available >= 2:
        values = [s.estimated_yes_probability for s in available]  # type: ignore[misc]
        spread = max(values) - min(values)
        agreement = round(max(0.0, 1 - spread / 0.5), 4)  # spread >= 0.5 -> 0 agreement
        agreement_score = agreement * 100

    stability_score = max(0.0, min(1.0, market_stability)) * 100

    confidence = round(
        data_quality.total * 0.35 + coverage_score * 0.25 + agreement_score * 0.25 + stability_score * 0.15,
        1,
    )
    if not deadline_phase_known:
        confidence = round(confidence * 0.9, 1)  # small penalty for unknown resolution timing

    return min(100.0, confidence), agreement
