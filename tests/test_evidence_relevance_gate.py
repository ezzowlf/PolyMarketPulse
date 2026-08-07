"""Permanent regression test for a real, observed failure mode: an
unrelated but positively-toned article ("President Trump and Republicans
Deliver Big Wins for the Silver State") was being scored as YES-evidence
for the market "Trump out as President by August 31?" purely because it
mentioned "Trump" positively — sentiment about the subject entity is not
evidence about the specific proposition the market resolves on.

Root cause: the sentiment-only fallback for `matched_condition` had no
relevance gate, and the minimum-evidence-count check counted *any* linked
article, not just ones that actually said something about the resolution
condition. Both are fixed in evidence.py; this file locks the fix in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _link_news(storage: Storage, market: Market, title: str, source: str, source_url: str, confidence: float, hours_ago: float = 1.0) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago), fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("trump",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


def _trump_market() -> Market:
    return Market(
        provider="polymarket", provider_market_id="trump-1", condition_id="",
        question="Trump out as President by August 31?", slug="trump-1",
    )


def test_single_loosely_relevant_positive_article_does_not_move_probability(storage: Storage) -> None:
    """The exact regression case: one weakly-linked (low term-overlap),
    positively-toned article about the subject entity, with no real
    connection to the resolution condition, must not produce a confident
    independent probability."""
    market = _trump_market()
    _link_news(
        storage, market, "President Trump and Republicans Deliver Big Wins for the Silver State",
        "whitehouse", "https://whitehouse.gov/nevada-wins", confidence=0.15,
    )
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="trump-1",
        question=market.question, resolution_text=None, market_yes_price=0.007,
    )
    assert result.available is False
    assert result.independent_yes_probability is None


def test_two_loosely_relevant_positive_articles_still_gated_by_relevance(storage: Storage) -> None:
    """Even with two linked articles (passing the raw MIN_EVIDENCE_ITEMS
    count), low relevance (link_confidence below the gate) must keep the
    sentiment fallback from firing, so neither counts as scored evidence."""
    market = _trump_market()
    _link_news(
        storage, market, "President Trump and Republicans Deliver Big Wins for the Silver State",
        "whitehouse", "https://whitehouse.gov/a", confidence=0.15, hours_ago=1,
    )
    _link_news(
        storage, market, "Manufacturing Jobs Flock to the U.S. Thanks to President Trump's Agenda",
        "whitehouse", "https://whitehouse.gov/b", confidence=0.15, hours_ago=2,
    )
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="trump-1",
        question=market.question, resolution_text=None, market_yes_price=0.007,
    )
    assert result.available is False


def test_two_relevant_and_directly_toned_articles_do_produce_an_estimate(storage: Storage) -> None:
    """Sanity check the other direction: genuinely relevant, well-linked
    evidence must still work — the fix is a relevance gate, not a blanket
    ban on the sentiment fallback."""
    market = _trump_market()
    _link_news(
        storage, market, "Trump resignation confirmed by White House officials",
        "reuters", "https://reuters.com/a", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "White House confirms Trump resignation agreement signed",
        "apnews", "https://apnews.com/b", confidence=0.6, hours_ago=2,
    )
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="trump-1",
        question=market.question, resolution_text=None, market_yes_price=0.007,
    )
    assert result.available is True
    assert result.independent_yes_probability is not None
    assert result.independent_yes_probability > 0.5


def test_relevance_scales_bayesian_evidence_strength(storage: Storage) -> None:
    """Same directional evidence, higher average relevance -> the estimate
    should move further from the neutral 0.5 prior than the same evidence
    at lower (but still gate-passing) relevance."""
    high_rel_market = Market(
        provider="polymarket", provider_market_id="high-rel", condition_id="",
        question="Will the ceasefire hold?", slug="high-rel",
    )
    low_rel_market = Market(
        provider="polymarket", provider_market_id="low-rel", condition_id="",
        question="Will the ceasefire hold?", slug="low-rel",
    )
    for pmid, market, conf in (("high-rel", high_rel_market, 0.9), ("low-rel", low_rel_market, 0.36)):
        _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", f"https://reuters.com/{pmid}-a", confidence=conf, hours_ago=1)
        _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", f"https://apnews.com/{pmid}-b", confidence=conf, hours_ago=2)

    high = compute_independent_evidence(storage.connection, "polymarket", "high-rel", high_rel_market.question, None, 0.5)
    low = compute_independent_evidence(storage.connection, "polymarket", "low-rel", low_rel_market.question, None, 0.5)
    assert high.available is True and low.available is True
    assert abs(high.independent_yes_probability - 0.5) > abs(low.independent_yes_probability - 0.5)
