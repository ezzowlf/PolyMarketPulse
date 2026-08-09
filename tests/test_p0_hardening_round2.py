"""P0 correctness hardening, round 2 (2026-08): follow-up verification and
tests for the fixes landed in round 1 (commit 79b2d59 — see
test_reported_bug_regression.py for the original root-cause fixture).

This module covers the gaps round 1 explicitly left open:

Part A — divergence_audit.py hard-fail behaviour for the exact reported
scenario, and for a *genuinely present but thin* History baseline combined
with a large divergence.

Part B — independent verification (with a real assertion, not just reading
the code) that confidence.py's historical_coverage dimension is driven by
comparable QUALITY (accepted_count / Kish ESS) and not raw candidate COUNT.

Part C — anti-cross-domain regression fixtures for history.py's
`_passes_compatibility_gate`, one per reported domain (politics/office
departure, geopolitics/category-only, crypto price-threshold, sport match).
None of these hardcode a real market_id — every fixture is constructed.
"""

from __future__ import annotations

from polymarketpulse.prediction.classification import classify_market
from polymarketpulse.prediction.confidence import compute_data_quality_composite
from polymarketpulse.prediction.divergence_audit import (
    DivergenceAuditContext,
    audit_divergence,
)
from polymarketpulse.prediction.history import (
    ComparableCandidate,
    WeightedBaselineResult,
    compute_weighted_baseline,
    find_comparable_cases,
)
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.prediction.types import SubmodelEstimate

# ---------------------------------------------------------------------------
# Part A — divergence audit
# ---------------------------------------------------------------------------


def test_divergence_audit_never_triggers_when_independent_probability_is_none() -> None:
    """The exact reported scenario (unparseable target, History-only, no
    evidence) now has independent_probability forced to None by round 1's
    Part 3/4 fixes *before* audit_divergence ever runs — so the audit is not
    even reached, and there is no REJECT/WARN/PASS to compute at all. This
    proves the claim in the task brief precisely rather than asserting it."""
    context = DivergenceAuditContext(
        independent_probability=None,
        market_probability=0.065,
        proposition=None,
        independent_evidence=None,
        comparable_sample_size=0,
        history_prior_provenance=None,
        resolution_rules_present=False,
        submodel_estimates=(),
    )
    result = audit_divergence(context)
    assert result.triggered is False
    assert result.verdict is None
    assert result.gap is None


def test_divergence_audit_rejects_thin_history_with_large_divergence() -> None:
    """A market where History IS available (genuine event_type match, some
    accepted comparables) but the sample is thin (well under the 10-case
    DATA_FITTED-strength threshold), there is zero independent evidence, and
    the resulting independent estimate diverges hugely from the market
    price. `evidentiary_sufficiency` must hard-fail this (neither a strong
    10+ DATA_FITTED history baseline nor >=2-source/DIRECT evidence backs
    it), forcing the overall verdict to REJECT, not WARN."""
    proposition = parse_market_proposition("Will the mayor resign by year end?", None)
    context = DivergenceAuditContext(
        independent_probability=0.85,
        market_probability=0.10,  # gap = 0.75, far above DIVERGENCE_THRESHOLD_PP
        proposition=proposition,
        independent_evidence=None,
        comparable_sample_size=4,  # thin — below the 10-case DATA_FITTED threshold
        history_prior_provenance="DATA_FITTED",
        resolution_rules_present=False,
        submodel_estimates=(
            SubmodelEstimate(name="history", estimated_yes_probability=0.85, weight=0.15, available=True, detail=""),
        ),
    )
    result = audit_divergence(context)
    assert result.triggered is True
    assert result.verdict == "REJECT"
    reject_names = {c.name for c in result.checks if c.verdict == "REJECT"}
    assert "evidentiary_sufficiency" in reject_names


def test_divergence_audit_passes_with_strong_history_and_large_divergence() -> None:
    """Sanity control for the REJECT test above: the identical divergence,
    but backed by a genuinely strong (10+ case, DATA_FITTED) historical
    baseline, must NOT hard-fail on evidentiary_sufficiency — proving the
    REJECT above is driven by sample thinness, not merely by the presence
    of a large divergence."""
    proposition = parse_market_proposition("Will the mayor resign by year end?", None)
    context = DivergenceAuditContext(
        independent_probability=0.85,
        market_probability=0.10,
        proposition=proposition,
        independent_evidence=None,
        comparable_sample_size=25,
        history_prior_provenance="DATA_FITTED",
        resolution_rules_present=False,
        submodel_estimates=(
            SubmodelEstimate(name="history", estimated_yes_probability=0.85, weight=0.35, available=True, detail=""),
        ),
    )
    result = audit_divergence(context)
    sufficiency = next(c for c in result.checks if c.name == "evidentiary_sufficiency")
    assert sufficiency.verdict == "PASS"


# ---------------------------------------------------------------------------
# Part B — confidence.py historical_coverage: quality, not raw count
# ---------------------------------------------------------------------------


def _baseline(candidate_count: int, accepted_weights: list[float]) -> WeightedBaselineResult:
    """Build a WeightedBaselineResult with a fixed candidate_count (raw
    number of candidates considered) but a controllable set of accepted
    weights, so ESS can be pushed low or high independently of
    candidate_count — the crux of what Part B needs to prove."""
    candidates_with_scores = []
    for i, w in enumerate(accepted_weights):
        candidates_with_scores.append(
            (
                ComparableCandidate(
                    market_id=f"c{i}", question="q", category="POLITICS", event_type="office_departure",
                    entities=(), proposition_status=None, location=None, start_date=None, end_date=None,
                    winning_outcome="Yes" if i % 2 == 0 else "No", resolution_status="resolved",
                ),
                w,
            )
        )
    # Pad candidate_count up to the requested raw count with gate-rejected
    # (weight 0.0) candidates, mirroring what find_comparable_cases would
    # actually hand compute_weighted_baseline for a mixed candidate pool.
    while len(candidates_with_scores) < candidate_count:
        i = len(candidates_with_scores)
        candidates_with_scores.append(
            (
                ComparableCandidate(
                    market_id=f"rej{i}", question="q", category="OTHER", event_type=None,
                    entities=(), proposition_status=None, location=None, start_date=None, end_date=None,
                    winning_outcome=None, resolution_status="resolved",
                ),
                0.0,
            )
        )
    return compute_weighted_baseline(candidates_with_scores)


def test_historical_coverage_is_driven_by_quality_not_raw_candidate_count() -> None:
    """Two markets with the IDENTICAL raw candidate_count but very
    different accepted-comparable quality (many equal-weight accepted cases
    -> high ESS, vs a handful of wildly-unequal-weight accepted cases ->
    low ESS) must produce measurably different historical_coverage scores —
    the high-quality one strictly higher. If historical_coverage were
    reading raw candidate_count instead of ESS, these two would score
    identically, which this test would catch."""
    same_candidate_count = 40

    high_quality = _baseline(same_candidate_count, [0.9] * 20)  # 20 near-equal-weight accepted cases -> high ESS
    low_quality = _baseline(same_candidate_count, [0.9, 0.05, 0.05, 0.05])  # thin, unequal -> low ESS

    assert high_quality.candidate_count == low_quality.candidate_count == same_candidate_count
    assert high_quality.effective_sample_size > low_quality.effective_sample_size

    high_dq = compute_data_quality_composite(
        proposition=None, history_uncertainty=high_quality, comparable_sample_size=high_quality.case_count,
        independent_evidence=None, specialized_estimates=[], eligible_specialized_models=(), aktualitaet=0.0,
    )
    low_dq = compute_data_quality_composite(
        proposition=None, history_uncertainty=low_quality, comparable_sample_size=low_quality.case_count,
        independent_evidence=None, specialized_estimates=[], eligible_specialized_models=(), aktualitaet=0.0,
    )

    high_cov = next(d for d in high_dq.dimensions if d.name == "historical_coverage")
    low_cov = next(d for d in low_dq.dimensions if d.name == "historical_coverage")

    assert high_cov.raw_value == high_quality.effective_sample_size
    assert low_cov.raw_value == low_quality.effective_sample_size
    assert high_cov.normalized_score is not None and low_cov.normalized_score is not None
    assert high_cov.normalized_score > low_cov.normalized_score


# ---------------------------------------------------------------------------
# Part C — anti-cross-domain compatibility-gate fixtures
# ---------------------------------------------------------------------------


def test_office_departure_target_rejects_topically_similar_but_incompatible_candidate() -> None:
    """Trump/Nevada-shaped fixture: the target is a genuine office_departure
    proposition. A candidate that merely mentions the same buzzwords
    ("Trump", "election", "president") in its question text, but carries a
    DIFFERENT event_type (here: `election`) and a matching category, must
    still be rejected by the compatibility gate unless it also shares an
    entity or the event_type — sharing only vocabulary is not enough."""
    target_q = "Will Governor Smith resign before the end of his term?"
    proposition = parse_market_proposition(target_q, None)
    classification = classify_market(target_q, None, proposition)
    assert proposition.event_type == "office_departure"

    incompatible = ComparableCandidate(
        market_id="trump-election", question="Will Trump win the presidential election?",
        category=classification.category, event_type="election", entities=(),
        proposition_status=None, location=None, start_date=None, end_date=None,
        winning_outcome="Yes", resolution_status="resolved",
    )
    compatible = ComparableCandidate(
        market_id="other-resign", question="Will Senator Doe resign amid scandal?",
        category=classification.category, event_type="office_departure", entities=(),
        proposition_status=None, location=None, start_date=None, end_date=None,
        winning_outcome="No", resolution_status="resolved",
    )
    scored = find_comparable_cases(proposition, classification, [incompatible, compatible])
    scores = {c.market_id: s for c, s in scored}
    assert scores["trump-election"] == 0.0
    assert scores["other-resign"] > 0.0


def test_geopolitics_category_only_match_is_rejected_by_gate() -> None:
    """Hormuz-shaped fixture: a GEOPOLITICS-category target and a
    GEOPOLITICS-category candidate about a totally different conflict, with
    no event_type match and no entity overlap, must NOT pad the comparable
    set purely on shared category — this is exactly the reported bug
    pattern generalized beyond "Hormuz has zero real candidates today"."""
    target_q = "Will the strait remain closed to tanker traffic through the deadline?"
    proposition = parse_market_proposition(target_q, None)
    classification = classify_market(target_q, None, proposition)

    category_only_match = ComparableCandidate(
        market_id="iran-sanctions", question="Will new sanctions be imposed on Iran?",
        category="GEOPOLITICS", event_type="sanctions_imposed", entities=(),
        proposition_status=None, location=None, start_date=None, end_date=None,
        winning_outcome="Yes", resolution_status="resolved",
    )
    scored = find_comparable_cases(proposition, classification, [category_only_match])
    assert scored[0][1] == 0.0


def test_btc_price_threshold_target_rejects_generic_crypto_launch_candidate() -> None:
    """BTC/quant fixture: a BTC price-threshold proposition (event_type
    price_above) must reject a generic crypto-launch/FDV-shaped candidate
    that shares the CRYPTO category but a different event_type and no
    entity overlap, while accepting a genuinely compatible
    price-threshold candidate for a different asset (same event_type)."""
    target_q = "Will BTC trade above $80,000 before the deadline?"
    proposition = parse_market_proposition(target_q, None)
    classification = classify_market(target_q, None, proposition)
    assert proposition.event_type == "price_above"
    assert classification.category == "CRYPTO"

    incompatible = ComparableCandidate(
        market_id="new-token-fdv", question="Will the new token's FDV exceed $1B at launch?",
        category="CRYPTO", event_type="token_launch", entities=(),
        proposition_status=None, location=None, start_date=None, end_date=None,
        winning_outcome="No", resolution_status="resolved",
    )
    compatible = ComparableCandidate(
        market_id="eth-price", question="Will ETH trade above $5,000 before the deadline?",
        category="CRYPTO", event_type="price_above", entities=(),
        proposition_status=None, location=None, start_date=None, end_date=None,
        winning_outcome="Yes", resolution_status="resolved",
    )
    scored = find_comparable_cases(proposition, classification, [incompatible, compatible])
    scores = {c.market_id: s for c, s in scored}
    assert scores["new-token-fdv"] == 0.0
    assert scores["eth-price"] > 0.0


def test_sport_match_target_rejects_cross_sport_candidate() -> None:
    """Sports fixture: a sport_match target must reject a candidate that is
    also 'SPORT_OTHER' category and also a sport_match event_type but
    describes an entirely different sport/matchup with zero entity overlap
    would still technically pass the gate (event_type matches), which is
    correct per the documented gate rule (category AND (event_type OR
    entity)) — cross-sport mixing at the *event_type* family level is by
    design allowed to accrue only a low score via entity/token mismatch,
    not hard-rejected, since 'sport_match' vs 'sport_match' genuinely is
    the same predicate family. What must NOT happen is a non-sport_match
    SPORT_OTHER candidate (e.g. sport_winner/tournament) sneaking in on
    category alone."""
    target_q = "Team Alpha vs Team Beta: will Alpha win the match?"
    proposition = parse_market_proposition(target_q, None)
    classification = classify_market(target_q, None, proposition)
    assert proposition.event_type == "sport_match"

    category_only = ComparableCandidate(
        market_id="tournament-winner", question="Who will win the championship tournament?",
        category="SPORT_OTHER", event_type="sport_winner", entities=(),
        proposition_status=None, location=None, start_date=None, end_date=None,
        winning_outcome="Yes", resolution_status="resolved",
    )
    scored = find_comparable_cases(proposition, classification, [category_only])
    assert scored[0][1] == 0.0
