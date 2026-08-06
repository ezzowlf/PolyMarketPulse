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


def _link_news(storage: Storage, market: Market, title: str, source: str, source_url: str, hours_ago: float, confidence: float = 0.5) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("test",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


def _market() -> Market:
    return Market(
        provider="polymarket", provider_market_id="evidence-1", condition_id="",
        question="Will the ceasefire agreement be confirmed?", slug="evidence-1",
    )


def test_no_linked_news_is_unavailable(storage: Storage) -> None:
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question="Will the ceasefire agreement be confirmed?", resolution_text=None,
        market_yes_price=0.9,
    )
    assert result.available is False
    assert "keine unabhängige Schätzung möglich" in result.detail


def test_single_linked_news_item_still_unavailable(storage: Storage) -> None:
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by officials", "reuters", "https://reuters.com/a", hours_ago=2)
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.9,
    )
    assert result.available is False


def test_confirming_evidence_from_independent_sources_produces_estimate_not_anchored_to_market_price(
    storage: Storage,
) -> None:
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", "https://reuters.com/a", hours_ago=1)
    _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", "https://apnews.com/b", hours_ago=3)

    # Market price is deliberately extreme (0.05) — if the independent
    # estimate merely echoed the market, it would also sit near 0.05.
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.05,
    )
    assert result.available is True
    assert result.independent_yes_probability is not None
    # Positive-sentiment confirming evidence should push the independent
    # estimate above the neutral 0.5 prior, nowhere near the 0.05 market price.
    assert result.independent_yes_probability > 0.5
    assert result.confirmation_count >= 1
    assert result.divergence is not None
    assert result.divergence > 0
    assert result.information_edge_score is not None


def test_contradictory_evidence_is_flagged(storage: Storage) -> None:
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed, deal signed", "reuters", "https://reuters.com/a", hours_ago=1)
    _link_news(storage, market, "Ceasefire denied, talks collapse", "bbc", "https://bbc.com/b", hours_ago=1)

    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is True
    assert result.contradiction_detected is True


def test_unavailable_when_no_evidence_infrastructure(tmp_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    result = compute_independent_evidence(
        conn, provider="polymarket", provider_market_id="x", question="Q?",
        resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is False
    conn.close()
