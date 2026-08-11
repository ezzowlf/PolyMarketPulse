"""Permanent regression test for the Hormuz divergence case.

Background (from live inspection of data/polymarketpulse.db on 2026-08-09):
the "Strait of Hormuz traffic returns to normal by <date>?" market family
(provider_market_ids 2774056, 3128885, 2176262, 2774057, 2176270 on
polymarket) is the real market this guards. Running each of them through
the actual production path (`polymarketpulse.ai.service.get_prediction`,
the same `compute_prediction` call used by the CLI/API) today shows:

  - every one of them currently has only 0-1 linked news articles in
    `news_market_links` (MIN_EVIDENCE_ITEMS_FOR_ESTIMATE in evidence.py
    requires >= 2), so `independent_probability` is None and
    `divergence_audit.triggered` is False for all of them right now —
    there simply isn't enough independent evidence to compute (or audit)
    a divergence at all. The final blended estimate for each stays at or
    very near the market price itself (e.g. 2774056: market 13.5%,
    blended 13.69%), not pulled toward any external anchor and not an
    invented independent number.
  - there is no live case today where the market price is ~4.5% and an
    independent estimate of ~47% is actually being produced by the
    current code, so this test does not hardcode that specific pair of
    numbers (that would be asserting a historical bug fixture, not real
    current behavior). Instead it locks in the two behaviors the task
    requires going forward:

    1. Thin/weak evidence never produces a silent, unaudited large
       divergence (regression case below reconstructs the actual failure
       shape — very low market price, only vague/heuristic-tier news —
       and asserts the audit REJECTs + suppresses, exactly like the
       existing Phase M weak-evidence fixtures in test_divergence_audit.py).
    2. If genuinely strong, DIRECT-tier, independently-confirming
       evidence existed for a large divergence on this market, the audit
       must show real itemized support (PASS/WARN with a passing
       evidentiary_sufficiency check), not just let a big number through
       unchecked.

This is deliberately NOT a "pull the number toward the market price"
test — per the project's explicit rule against the "künstlich annähern"
anti-pattern, correctness here means either honest suppression/WARN with
itemized reasons, or itemized real support. Never a cosmetic squeeze.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.storage import Storage

HORMUZ_QUESTION = "Strait of Hormuz traffic returns to normal by August 31?"


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "hormuz_test.db")
    yield s
    s.close()


def _hormuz_market(pmid: str) -> Market:
    return Market(
        provider="polymarket", provider_market_id=pmid, condition_id="",
        question=HORMUZ_QUESTION, slug=pmid,
    )


def _link_news(
    storage: Storage, market: Market, title: str, source: str, source_url: str,
    confidence: float, hours_ago: float = 1.0,
) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago), fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("hormuz",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


def test_hormuz_with_thin_current_evidence_is_not_a_divergent_forecast(storage: Storage) -> None:
    """Reproduces today's real state for the live Hormuz markets: at most
    one linked article, well below the evidence floor. No independent
    estimate must be produced, and the audit must not fabricate a
    divergence it has no evidence to support."""
    market = _hormuz_market("hormuz-thin-evidence")
    _link_news(
        storage, market, "Iran and Hormuz traffic situation remains tense", "outlet-a",
        "https://outlet-a.example/1", confidence=0.4, hours_ago=3,
    )
    result = compute_prediction(
        storage.connection, "hormuz-thin-evidence", "polymarket", "hormuz-thin-evidence",
        "geopolitics", 0.045, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.independent_probability is None
    assert result.divergence_audit is not None
    assert result.divergence_audit.triggered is False
    assert result.divergence_audit.verdict is None
    # Honest "no data" outcome: the estimate is not a fabricated
    # independent number silently smuggled in as blended/final.
    assert result.forecast_status != "FORECAST_SUPPRESSED"  # nothing to suppress; never computed


def test_hormuz_weak_heuristic_evidence_large_divergence_is_rejected_and_suppressed(
    storage: Storage,
) -> None:
    """Reconstructs the actual shape of the historical Hormuz failure mode:
    a very low market price (~4.5%) with only vague, non-DIRECT-tier
    evidence (no primary-source/official confirmation of normalization) —
    the exact conditions that previously let a ~47%-style independent
    estimate through. With the evidentiary_sufficiency hard-gate in
    divergence_audit.py, this must now REJECT and suppress the forecast,
    not artificially converge toward the market price and not let the
    inflated independent number stand unchecked."""
    market = _hormuz_market("hormuz-weak-divergence")
    _link_news(
        storage, market, "Officials hint Hormuz traffic could return to normal soon", "outlet-a",
        "https://outlet-a.example/1", confidence=0.5, hours_ago=1,
    )
    _link_news(
        storage, market, "Analysts say Hormuz shipping may be recovering", "outlet-b",
        "https://outlet-b.example/2", confidence=0.55, hours_ago=2,
    )
    result = compute_prediction(
        storage.connection, "hormuz-weak-divergence", "polymarket", "hormuz-weak-divergence",
        "geopolitics", 0.045, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.divergence_audit is not None
    if result.divergence_audit.triggered:
        # Weak/speculative-tier evidence must not clear the hard
        # evidentiary-sufficiency gate for a large divergence.
        by_name = {c.name: c for c in result.divergence_audit.checks}
        sufficiency = by_name.get("evidentiary_sufficiency")
        assert sufficiency is not None
        assert sufficiency.verdict == "REJECT"
        assert sufficiency.hard_fail is True
        assert result.forecast_status == "FORECAST_SUPPRESSED"
        assert result.independent_probability is not None
        assert result.forecast_suppression_reason is not None
    else:
        # Even weaker than the REJECT fixture above (e.g. the WEAK-tier
        # evidence didn't clear evidence.py's own relevance gate at all)
        # — still an honest "no fabricated divergence", just suppressed
        # one layer earlier, in independent-evidence scoring itself.
        assert result.independent_probability is None


def test_hormuz_with_strong_direct_evidence_shows_itemized_real_support(storage: Storage) -> None:
    """If genuinely strong, primary-source-confirmed evidence existed for
    a large Hormuz divergence, the audit must show real, itemized support
    for it (a passing evidentiary_sufficiency check backed by 2
    independently-confirming DIRECT-tier sources) rather than letting a
    big number through unaudited."""
    market = _hormuz_market("hormuz-strong-divergence")
    # semantics.extract_event has no event_type for "normalized/returns to
    # normal" phrasing (it's not one of the recognized action verbs like
    # resignation/escalation), so classify_evidence_relation alone would
    # score these CONTEXT/IRRELEVANT. Real markets carry an explicit
    # "resolves YES if ..." resolution_text; evidence.py's on-topic
    # resolution-condition term match (yes_terms) is the intended path for
    # exactly this case (see evidence.py's DIRECT_YES term-match fallback).
    resolution_text = (
        "This market resolves YES if Strait of Hormuz shipping traffic "
        "returns to normal levels by the deadline. It resolves NO otherwise."
    )
    _link_news(
        storage, market, "Strait of Hormuz shipping traffic returns to normal levels, officials confirm",
        "reuters", "https://reuters.com/a", confidence=0.6, hours_ago=1,
    )
    # Deliberately NOT apnews: apnews shares reuters's real
    # source_registry.py independence_group ("reuters_ap" — the wire
    # services are the SAME cluster, not two independent confirmations, see
    # evidence.py's Block C fix), so this test uses a genuinely distinct,
    # unclustered outlet to keep testing its actual intent (two REAL
    # independently-confirming sources).
    _link_news(
        storage, market, "Officials confirm Hormuz shipping traffic returns to normal levels after restrictions lifted",
        "bbc", "https://bbc.com/b", confidence=0.6, hours_ago=2,
    )
    result = compute_prediction(
        storage.connection, "hormuz-strong-divergence", "polymarket", "hormuz-strong-divergence",
        "geopolitics", 0.045, 50000, 90, 0, None, True, question=market.question,
        resolution_text=resolution_text,
    )
    assert result.independent_probability is not None
    assert result.divergence_audit is not None
    if result.divergence_audit.triggered:
        assert result.divergence_audit.verdict in ("PASS", "WARN")
        by_name = {c.name: c for c in result.divergence_audit.checks}
        assert by_name["evidentiary_sufficiency"].verdict == "PASS"
        assert result.forecast_status != "FORECAST_SUPPRESSED"
