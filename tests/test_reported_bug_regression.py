"""Regression test for the reported P0 correctness bug (2026-08): a
"strategic waterway normalization"-shaped market showed market probability
6.5%, independent probability 39.8%, final 43.9%, recommendation
STRONG_YES — while simultaneously event_type=None, proposition=AMBIGUOUS,
no subject/event_type/yes_condition/no_condition detected, resolution rule
missing, deadline not parsed, independent evidence=0, news evidence=0,
event relations=0, History was the ONLY model used, and its "126
comparables" included obviously unrelated markets (indoor dining in NYC,
celebrity divorce, sports betting, token FDV markets) all at suspiciously
uniform ~33% similarity — the signature of a fallback/default similarity
score that didn't actually gate on relatedness.

This test does NOT hardcode the specific market_id from the report (per
the fix's explicit "general rule, not a specific-market patch"
requirement). Instead it constructs a fixture with the exact reported
symptom profile — an unparseable target proposition, no independent
evidence, and a History table stocked entirely with genuinely unrelated
comparable markets (different category, different event_type, no entity
overlap) — and asserts the FIXED pipeline:
  1. never lets an unrelated comparable enter the weighted baseline
     (Part 1's hard compatibility gate — accepted_count == 0),
  2. never produces a quantitative History probability for an
     unclassified/ambiguous target (Part 3),
  3. never publishes an independent_probability from History alone when
     the target proposition is unparseable (Part 4's history-only safety
     rule),
  4. never emits STRONG_YES/STRONG_NO in this scenario (Part 5's
     recommendation-strength gate),
  5. lands at forecast_status == NO_FORECAST, not a fabricated
     high-confidence number.
"""

from __future__ import annotations

import sqlite3

import pytest

from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.classification import classify_market
from polymarketpulse.prediction.history import (
    ComparableCandidate,
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


# Deliberately structurally unrelated comparables — mirrors the report's
# "indoor dining in NYC", "celebrity divorce", "GME/Robinhood", "LINK above
# $30", "Biden inauguration", "Mike Tyson boxing" examples: different
# category, different (or no) event_type, zero entity overlap with a
# geopolitical-waterway-normalization target.
_UNRELATED_COMPARABLES = [
    ("nyc-dining", "Will NYC allow indoor dining again by June?", "LOCAL_POLICY", None, "resolved", "Yes"),
    ("kk-divorce", "Will the celebrity divorce finalize this year?", "ENTERTAINMENT", None, "resolved", "No"),
    ("gme-robinhood", "Will Robinhood restrict GME trading again?", "FINANCE", None, "resolved", "No"),
    ("link-30", "Will LINK trade above $30 this year?", "CRYPTO", "price_above", "resolved", "No"),
    ("biden-inaug", "Will Biden attend the inauguration?", "POLITICS", None, "resolved", "Yes"),
    ("tyson-fight", "Will Mike Tyson win his boxing match?", "SPORT_OTHER", "sport_match", "resolved", "Yes"),
]


def _seed_unrelated(conn) -> None:
    now = "2026-01-01T00:00:00+00:00"
    for pmid, question, category, event_type, status, outcome in _UNRELATED_COMPARABLES:
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


# The unparseable geopolitical-waterway-style target question — deliberately
# vague enough that semantics.py cannot extract a subject/event_type (the
# report's "proposition=AMBIGUOUS, no subject/event_type detected" symptom).
_TARGET_QUESTION = "Will conditions normalize by year end?"
_TARGET_CATEGORY = "GEOPOLITICS"


def test_target_proposition_is_genuinely_unparseable() -> None:
    """Sanity check on the fixture itself: the target question must
    actually reproduce the reported "no event_type / AMBIGUOUS" shape, not
    accidentally parse cleanly."""
    proposition = parse_market_proposition(_TARGET_QUESTION, None)
    assert proposition.event_type is None
    assert proposition.proposition_status == "AMBIGUOUS"


def test_unrelated_comparables_are_gated_to_zero_weight(conn) -> None:
    """Part 1: history.py's compatibility gate must reject every one of
    these structurally-unrelated candidates outright (weight 0.0), not
    assign them a nonzero "default" similarity like the reported ~33%."""
    proposition = parse_market_proposition(_TARGET_QUESTION, None)
    classification = classify_market(_TARGET_QUESTION, None, proposition)
    candidates = [
        ComparableCandidate(
            market_id=pmid, question=question, category=category, event_type=event_type,
            entities=(), proposition_status=None, location=None, start_date=None, end_date=None,
            winning_outcome=outcome, resolution_status=status,
        )
        for pmid, question, category, event_type, status, outcome in _UNRELATED_COMPARABLES
    ]
    scored = find_comparable_cases(proposition, classification, candidates)
    assert all(score == 0.0 for _candidate, score in scored), scored

    result = compute_weighted_baseline(scored)
    assert result.accepted_count == 0
    assert result.candidate_count == len(_UNRELATED_COMPARABLES)
    assert result.baseline_yes_probability is None


def test_reported_bug_scenario_yields_no_forecast_not_strong_yes(conn) -> None:
    """The full end-to-end scenario: unparseable target proposition, no
    independent evidence, no event relations, History stocked with only
    unrelated comparables. The FIXED pipeline must refuse to publish a
    quantitative forecast at all — never STRONG_YES, never a fabricated
    independent_probability."""
    _seed_unrelated(conn)
    result = compute_prediction(
        conn,
        market_id="reported-bug-fixture",
        provider="polymarket",
        provider_market_id="reported-bug-fixture",
        category=_TARGET_CATEGORY,
        market_yes_price=0.065,  # mirrors the report's 6.5% market price
        liquidity=10000,
        data_quality_report_score=None,
        news_count=0,
        news_agreement=None,
        resolution_rules_present=False,
        question=_TARGET_QUESTION,
        resolution_text=None,
    )

    # Part 1/3/4: no fabricated independent probability.
    assert result.independent_probability is None
    assert result.comparable_sample_size == 0

    # Part 4/5: forecast status and recommendation must reflect "nothing to
    # publish", not a confident directional call.
    assert result.forecast_status == "NO_FORECAST"
    assert result.recommendation not in ("STRONG_YES", "STRONG_NO", "YES", "NO")

    # Part 7 (data-quality-from-quality-not-count): with zero accepted
    # comparables, the historical_coverage-driven composite must not read
    # as "Mittel"/"medium" data quality — data_quality_composite.total (0-1)
    # for a market with genuinely no usable evidence must be low.
    assert result.data_quality_composite is not None
    assert result.data_quality_composite.score < 50.0
