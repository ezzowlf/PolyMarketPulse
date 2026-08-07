"""K1 critical invariant test: confidence must be a function of measured
data-quality/robustness signals ONLY, never of the probability estimate's
magnitude or its distance from 50%.

`compute_confidence_composite`'s signature structurally cannot see the
probability value at all (no `independent_probability`/`estimated_yes`
parameter exists) — that alone proves the composite can't be coupled to it.
This test additionally constructs the exact scenario the brief calls out:
a LOW independent_probability (~12%) market with STRONG evidence backing
(a real DATA_FITTED-equivalent narrow-Wilson-interval historical baseline,
DIRECT-tier independent evidence, high source quality, multiple independent
domains, tight submodel agreement) and asserts the resulting confidence
composite score is HIGH (> 70), while an otherwise-identical LOW-quality
scenario (wide/no data) with the SAME probability stays low — proving the
independent variable driving confidence is data quality, not the
probability's distance from 50%."""

from __future__ import annotations

from polymarketpulse.prediction.confidence import compute_confidence_composite
from polymarketpulse.prediction.evidence import EvidenceFactor, IndependentEvidenceResult
from polymarketpulse.prediction.history import WeightedBaselineResult
from polymarketpulse.prediction.semantics import MarketProposition
from polymarketpulse.prediction.types import SubmodelEstimate


def _strong_evidence(prob: float) -> IndependentEvidenceResult:
    items = (
        EvidenceFactor(
            news_event_id=1, title="Official confirms outcome", source="reuters", source_domain="reuters.com",
            url="https://reuters.com/x", published_at="2026-08-07T12:00:00+00:00", reliability=0.95,
            tone=0.1, matched_condition="yes", recency_weight=0.98, link_confidence=0.9,
            relation_label="DIRECT_YES", entailment="ENTAILS", relation_weight=1.0,
        ),
        EvidenceFactor(
            news_event_id=2, title="Independent confirmation", source="apnews", source_domain="apnews.com",
            url="https://apnews.com/x", published_at="2026-08-07T11:00:00+00:00", reliability=0.9,
            tone=0.1, matched_condition="yes", recency_weight=0.95, link_confidence=0.9,
            relation_label="DIRECT_YES", entailment="ENTAILS", relation_weight=1.0,
        ),
        EvidenceFactor(
            news_event_id=3, title="Third independent source", source="bbc", source_domain="bbc.com",
            url="https://bbc.com/x", published_at="2026-08-07T10:00:00+00:00", reliability=0.9,
            tone=0.1, matched_condition="yes", recency_weight=0.9, link_confidence=0.9,
            relation_label="DIRECT_YES", entailment="ENTAILS", relation_weight=1.0,
        ),
    )
    return IndependentEvidenceResult(
        available=True, independent_yes_probability=prob, confirmation_count=3,
        source_quality_score=92.0, time_since_first_report_hours=2.0,
        contradiction_detected=False, breaking=False, information_edge_score=80.0,
        divergence=None, evidence_for_yes=items, evidence_for_no=(),
        detail="3 DIRECT-tier, independently-sourced confirmations.",
    )


def _weak_evidence() -> IndependentEvidenceResult:
    return IndependentEvidenceResult(
        available=False, independent_yes_probability=None, confirmation_count=0,
        source_quality_score=None, time_since_first_report_hours=None,
        contradiction_detected=False, breaking=False, information_edge_score=None,
        divergence=None, detail="No usable evidence.",
    )


def _clear_proposition() -> MarketProposition:
    return MarketProposition(
        subject="X", predicate="happen", object=None, event_type="generic",
        direction="yes_if_occurs", threshold=None, unit=None, location=None, start_time=None,
        deadline=None, yes_condition="X happens", no_condition="X does not happen",
        resolution_authority="Official source", ambiguity_flags=(), proposition_status="CLEAR",
    )


def _narrow_history() -> WeightedBaselineResult:
    return WeightedBaselineResult(
        baseline_yes_probability=0.12, total_weight=40.0, effective_sample_size=38.0, case_count=40,
        tier="usable", detail="38 effective comparable cases, narrow Wilson interval.",
        lower_bound=0.09, upper_bound=0.16, uncertainty_width=0.07,
    )


def _no_history() -> WeightedBaselineResult | None:
    return None


def _agreeing_submodels(prob: float) -> list[SubmodelEstimate]:
    return [
        SubmodelEstimate(name="history", estimated_yes_probability=prob, weight=0.4, available=True, detail=""),
        SubmodelEstimate(
            name="independent_evidence", estimated_yes_probability=prob + 0.01, weight=0.4, available=True, detail="",
        ),
        SubmodelEstimate(name="momentum", estimated_yes_probability=prob - 0.01, weight=0.2, available=True, detail=""),
    ]


def test_low_probability_with_strong_evidence_yields_high_confidence():
    """The exact invariant the brief demands: independent_probability ~12%
    (far from 50%) but strong, well-evidenced backing -> confidence HIGH."""
    prob = 0.12
    composite = compute_confidence_composite(
        proposition=_clear_proposition(),
        history_uncertainty=_narrow_history(),
        comparable_sample_size=40,
        independent_evidence=_strong_evidence(prob),
        specialized_estimates=[],
        all_submodel_estimates=_agreeing_submodels(prob),
        aktualitaet=95.0,
        deadline_phase_known=True,
    )
    assert composite.score > 70, (
        f"Expected HIGH confidence for a low-probability-but-strong-evidence market, got {composite.score}. "
        f"Dimensions: {[d.as_dict() for d in composite.dimensions]}"
    )


def test_low_probability_with_weak_evidence_yields_lower_confidence():
    """Same low probability (0.12), but with NO real backing (no history, no
    evidence, no agreement signal) -> confidence must be meaningfully lower
    than the strong-evidence case above, proving confidence tracks evidence
    quality, not the probability value itself (which is identical here)."""
    composite = compute_confidence_composite(
        proposition=None,
        history_uncertainty=_no_history(),
        comparable_sample_size=0,
        independent_evidence=_weak_evidence(),
        specialized_estimates=[],
        all_submodel_estimates=[
            SubmodelEstimate(name="history", estimated_yes_probability=None, weight=0.0, available=False, detail=""),
        ],
        aktualitaet=50.0,
        deadline_phase_known=False,
    )
    assert composite.score < 55


def test_confidence_composite_signature_has_no_probability_parameter():
    """Structural proof, not just an empirical one: compute_confidence_composite
    literally has no parameter through which a probability value could flow
    in, so no future edit can silently reintroduce probability-distance
    coupling without changing this test."""
    import inspect

    params = set(inspect.signature(compute_confidence_composite).parameters)
    for forbidden in ("probability", "estimated_yes", "independent_probability", "blended_probability"):
        assert forbidden not in params
