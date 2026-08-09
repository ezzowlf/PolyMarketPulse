"""P0 follow-up round (2026-08-10): closes the gaps explicitly left open by
the first fix (commit 79b2d59, see tests/test_reported_bug_regression.py).

Part A: divergence_audit end-to-end behavior for the exact reported
scenario (independent_probability already None => audit never triggers),
plus a separate case where some comparables DO pass the new compatibility
gate but ESS is low and divergence is large => verdict REJECT.

Part B: confidence.py's historical_coverage dimension must be driven by
WeightedBaselineResult.effective_sample_size (a quality/ESS signal), not a
raw/stale comparable_count -- two fixtures with identical raw candidate
counts but very different accepted/ESS must score differently.

Part C: four regression tests targeting history._passes_compatibility_gate
specifically (not the evidence/news relevance gate in test_semantics.py /
test_evidence_relevance_gate.py) -- textual/category-only relatedness must
not be enough to pass the gate.
"""

from __future__ import annotations

import sqlite3

import pytest

from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.classification import classify_market
from polymarketpulse.prediction.confidence import compute_data_quality_composite
from polymarketpulse.prediction.divergence_audit import (
    DivergenceAuditContext,
    audit_divergence,
)
from polymarketpulse.prediction.history import (
    ComparableCandidate,
    _passes_compatibility_gate,
    compute_weighted_baseline,
    find_comparable_cases,
)
from polymarketpulse.prediction.semantics import parse_market_proposition


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE markets (
            market_id TEXT PRIMARY KEY, provider TEXT, provider_market_id TEXT,
            condition_id TEXT, question TEXT, slug TEXT, category TEXT,
            classified_category TEXT, event_type TEXT, entities_json TEXT,
            proposition_json TEXT, start_date TEXT, end_date TEXT,
            url TEXT, first_seen_at TEXT, last_seen_at TEXT,
            resolution_status TEXT DEFAULT 'resolved'
        );
        CREATE TABLE market_resolutions (
            provider TEXT, provider_market_id TEXT, status TEXT, winning_outcome TEXT,
            resolved_at TEXT, detected_at TEXT
        );
        CREATE TABLE market_snapshots (
            market_id TEXT, yes_price REAL, no_price REAL, best_bid REAL, best_ask REAL,
            liquidity REAL, volume_24h REAL, volume_total REAL, spread REAL,
            one_day_change REAL, opportunity_score REAL, captured_at TEXT
        );
        """
    )
    return c


# ---------------------------------------------------------------------------
# Part A1: exact reported scenario -- audit_divergence never even needs to
# fire REJECT because independent_probability is already None.
# ---------------------------------------------------------------------------

_TARGET_QUESTION = "Will conditions normalize by year end?"
_TARGET_CATEGORY = "GEOPOLITICS"

_UNRELATED_COMPARABLES = [
    ("nyc-dining", "Will NYC allow indoor dining again by June?", "LOCAL_POLICY", None, "resolved", "Yes"),
    ("kk-divorce", "Will the celebrity divorce finalize this year?", "ENTERTAINMENT", None, "resolved", "No"),
    ("tyson-fight", "Will Mike Tyson win his boxing match?", "SPORT_OTHER", "sport_match", "resolved", "Yes"),
]


def _seed(conn, rows) -> None:
    now = "2026-01-01T00:00:00+00:00"
    for pmid, question, category, event_type, status, outcome in rows:
        conn.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, condition_id, question, slug, "
            "category, classified_category, event_type, url, first_seen_at, last_seen_at) "
            "VALUES (?, 'polymarket', ?, '', ?, ?, ?, ?, ?, 'https://x', ?, ?)",
            (pmid, pmid, question, pmid, category, category, event_type, now, now),
        )
        conn.execute(
            "INSERT INTO market_resolutions (provider, provider_market_id, status, winning_outcome, "
            "resolved_at, detected_at) VALUES ('polymarket', ?, ?, ?, ?, ?)",
            (pmid, status, outcome, now, now),
        )
    conn.commit()


def test_part_a1_audit_never_triggers_when_independent_probability_is_none(conn) -> None:
    """End-to-end: compute_prediction on the exact reported-bug-shaped
    fixture yields independent_probability=None, and calling
    audit_divergence directly with that None confirms it short-circuits
    (triggered=False, verdict=None) -- there is nothing for a REJECT verdict
    to even apply to."""
    _seed(conn, _UNRELATED_COMPARABLES)
    result = compute_prediction(
        conn,
        market_id="reported-bug-fixture-2",
        provider="polymarket",
        provider_market_id="reported-bug-fixture-2",
        category=_TARGET_CATEGORY,
        market_yes_price=0.065,
        liquidity=10000,
        data_quality_report_score=None,
        news_count=0,
        news_agreement=None,
        resolution_rules_present=False,
        question=_TARGET_QUESTION,
        resolution_text=None,
    )
    assert result.independent_probability is None

    audit = audit_divergence(
        DivergenceAuditContext(
            independent_probability=result.independent_probability,
            market_probability=0.065,
            proposition=None,
            independent_evidence=None,
            comparable_sample_size=result.comparable_sample_size,
            history_prior_provenance=None,
            resolution_rules_present=False,
            submodel_estimates=(),
        )
    )
    assert audit.triggered is False
    assert audit.verdict is None


# ---------------------------------------------------------------------------
# Part A2: some comparables genuinely pass the new gate, but ESS is low and
# divergence is large -- verdict must be REJECT (hard-fail on
# evidentiary_sufficiency), not a soft WARN.
# ---------------------------------------------------------------------------


def test_part_a2_low_ess_accepted_comparables_with_large_divergence_is_reject() -> None:
    proposition = parse_market_proposition(
        "Will the president leave office before the term ends?", None
    )
    classification = classify_market(
        "Will the president leave office before the term ends?", None, proposition
    )
    assert proposition.event_type == "office_departure"

    # Only 2 candidates pass the gate (category+event_type match) -- far
    # below the 10-case DATA_FITTED threshold, so this is a thin,
    # low-confidence baseline even though it's a *genuine* match.
    candidates = [
        ComparableCandidate(
            market_id="c1", question="Will the prime minister leave office early?",
            category="POLITICS", event_type="office_departure", entities=(),
            proposition_status=None, location=None, start_date=None, end_date=None,
            winning_outcome="Yes", resolution_status="resolved",
        ),
        ComparableCandidate(
            market_id="c2", question="Will the chancellor leave office early?",
            category="POLITICS", event_type="office_departure", entities=(),
            proposition_status=None, location=None, start_date=None, end_date=None,
            winning_outcome="Yes", resolution_status="resolved",
        ),
    ]
    scored = find_comparable_cases(proposition, classification, candidates)
    assert all(score > 0 for _c, score in scored), "genuine matches must pass the gate"

    baseline = compute_weighted_baseline(scored)
    assert baseline.accepted_count == 2
    assert baseline.effective_sample_size < 10  # thin sample
    assert baseline.baseline_yes_probability is not None

    audit = audit_divergence(
        DivergenceAuditContext(
            independent_probability=baseline.baseline_yes_probability,  # ~1.0
            market_probability=0.10,  # large divergence vs a thin, non-DATA_FITTED-tier baseline
            proposition=proposition,
            independent_evidence=None,
            comparable_sample_size=baseline.case_count,
            history_prior_provenance="DATA_FITTED",
            resolution_rules_present=False,
            submodel_estimates=(),
        )
    )
    assert audit.triggered is True
    assert audit.verdict == "REJECT"
    hard_fails = [c for c in audit.checks if c.hard_fail and c.verdict == "REJECT"]
    assert any(c.name == "evidentiary_sufficiency" for c in hard_fails)


# ---------------------------------------------------------------------------
# Part B: historical_coverage dimension must be ESS/quality-driven, not a
# raw/stale comparable_count.
# ---------------------------------------------------------------------------


def test_part_b_historical_coverage_uses_ess_not_raw_count() -> None:
    # Both fixtures present the SAME raw candidate_count (20) to
    # compute_weighted_baseline. Fixture 1: only 2 candidates pass the
    # compatibility gate (weight > 0), the other 18 are gate-rejected
    # (weight 0.0) -- a thin, low-ESS baseline. Fixture 2: all 20 pass the
    # gate at equal weight -- a much higher-ESS baseline. This is exactly
    # the "identical raw candidate_count, very different accepted_count/ESS"
    # shape the task requires.
    def _fake_scored(weighted_pairs):
        out = []
        for i, w in enumerate(weighted_pairs):
            cand = ComparableCandidate(
                market_id=f"m{i}", question="q", category="POLITICS", event_type="office_departure",
                entities=(), proposition_status=None, location=None, start_date=None, end_date=None,
                winning_outcome="Yes", resolution_status="resolved",
            )
            out.append((cand, w))
        return out

    low_ess_weights = [0.3, 0.3] + [0.0] * 18  # 2 accepted, 18 gate-rejected
    high_ess_weights = [0.3] * 20  # all 20 accepted at equal weight

    low_ess_result = compute_weighted_baseline(_fake_scored(low_ess_weights))
    high_ess_result = compute_weighted_baseline(_fake_scored(high_ess_weights))
    assert low_ess_result.candidate_count == high_ess_result.candidate_count == 20
    assert low_ess_result.accepted_count == 2
    assert high_ess_result.accepted_count == 20
    assert low_ess_result.effective_sample_size < high_ess_result.effective_sample_size

    dq_low = compute_data_quality_composite(
        proposition=None, history_uncertainty=low_ess_result,
        comparable_sample_size=low_ess_result.case_count, independent_evidence=None,
        specialized_estimates=[], eligible_specialized_models=(), aktualitaet=50.0,
    )
    dq_high = compute_data_quality_composite(
        proposition=None, history_uncertainty=high_ess_result,
        comparable_sample_size=high_ess_result.case_count, independent_evidence=None,
        specialized_estimates=[], eligible_specialized_models=(), aktualitaet=50.0,
    )
    hist_dim_low = next(d for d in dq_low.dimensions if d.name == "historical_coverage")
    hist_dim_high = next(d for d in dq_high.dimensions if d.name == "historical_coverage")

    # The dimension's raw_value must literally be the ESS, not case_count.
    assert hist_dim_low.raw_value == low_ess_result.effective_sample_size
    assert hist_dim_high.raw_value == high_ess_result.effective_sample_size
    assert hist_dim_low.raw_value != 20
    assert hist_dim_high.normalized_score > hist_dim_low.normalized_score
    # And therefore the composite score itself must measurably differ.
    assert dq_high.score > dq_low.score


# ---------------------------------------------------------------------------
# Part C: four regression tests targeting _passes_compatibility_gate
# specifically.
# ---------------------------------------------------------------------------


def test_part_c1_trump_office_departure_textual_overlap_rejected_without_event_type_match() -> None:
    """A candidate mentions Trump/election/president in its question text
    but is category=ENTERTAINMENT (not POLITICS) and has no event_type --
    the gate must reject it despite the textual relatedness."""
    proposition = parse_market_proposition("Will the president leave office before term end?", None)
    classification = classify_market("Will the president leave office before term end?", None, proposition)
    assert classification.category == "POLITICS"
    assert proposition.event_type == "office_departure"

    candidate = ComparableCandidate(
        market_id="trump-doc", question="Will Trump be featured in a new election documentary?",
        category="ENTERTAINMENT", event_type=None, entities=(), proposition_status=None,
        location=None, start_date=None, end_date=None, winning_outcome="Yes", resolution_status="resolved",
    )
    target_entities: set[str] = set()
    assert _passes_compatibility_gate(proposition, classification, target_entities, candidate) is False


def test_part_c2_hormuz_strategic_waterway_category_only_match_rejected_different_event_type() -> None:
    """Both target and candidate classify as WAR_PEACE (this taxonomy's
    category for both `strategic_waterway` and `sanctions` event_types --
    see classification._EVENT_TYPE_CATEGORY), but the candidate's
    event_type (sanctions) differs from the target's (strategic_waterway)
    and there is no entity overlap -- category-only match must not be
    enough, exactly the Hormuz-report failure mode (category matched,
    event_type/entities did not)."""
    question = "Will the strait remain open to shipping through year end?"
    proposition = parse_market_proposition(question, None)
    classification = classify_market(question, None, proposition)
    assert proposition.event_type == "strategic_waterway"
    assert classification.category == "WAR_PEACE"

    candidate = ComparableCandidate(
        market_id="sanctions-1", question="Will new sanctions be imposed on the regime?",
        category="WAR_PEACE", event_type="sanctions", entities=(), proposition_status=None,
        location=None, start_date=None, end_date=None, winning_outcome="Yes", resolution_status="resolved",
    )
    target_entities: set[str] = set()
    assert _passes_compatibility_gate(proposition, classification, target_entities, candidate) is False


def test_part_c3_btc_price_threshold_rejects_fdv_launch_comparable() -> None:
    """A crypto-launch/FDV-style candidate (different event_type) must be
    rejected as a comparable for a BTC price-threshold (price_above)
    market, even though both are CRYPTO category."""
    question = "Will BTC trade above $100,000 by year end?"
    proposition = parse_market_proposition(question, None)
    classification = classify_market(question, None, proposition)
    assert classification.category == "CRYPTO"
    assert proposition.event_type == "price_above"

    candidate = ComparableCandidate(
        market_id="new-token-fdv", question="Will the new token's FDV exceed $1B at launch?",
        category="CRYPTO", event_type="token_launch", entities=(), proposition_status=None,
        location=None, start_date=None, end_date=None, winning_outcome="No", resolution_status="resolved",
    )
    target_entities: set[str] = set()
    assert _passes_compatibility_gate(proposition, classification, target_entities, candidate) is False


def test_part_c4_different_sport_and_structure_rejected() -> None:
    """A different-sport / different-structure-type candidate must be
    rejected as a comparable for a sport_match proposition -- category
    match (SPORT_OTHER) alone is insufficient without event_type match or
    entity overlap."""
    question = "Will Fighter A vs Fighter B end with a match win for Fighter A?"
    proposition = parse_market_proposition(question, None)
    classification = classify_market(question, None, proposition)
    assert proposition.event_type == "sport_match"
    assert classification.category == "SPORT_OTHER"

    candidate = ComparableCandidate(
        market_id="tennis-tourney", question="Will the top seed win the tournament?",
        category="SPORT_OTHER", event_type="sport_tournament", entities=(), proposition_status=None,
        location=None, start_date=None, end_date=None, winning_outcome="Yes", resolution_status="resolved",
    )
    target_entities: set[str] = set()
    assert _passes_compatibility_gate(proposition, classification, target_entities, candidate) is False
