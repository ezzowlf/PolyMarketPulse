from datetime import UTC, datetime

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import link_news_to_markets
from polymarketpulse.news.rss import fetch_feed


def test_news_event_content_hash_deduplicates_identical_items() -> None:
    a = NewsEvent(source="x", source_url="https://x/1", title="Fed cuts rates", published_at=None, fetched_at=datetime.now(UTC))
    b = NewsEvent(source="x", source_url="https://x/1", title="Fed cuts rates", published_at=None, fetched_at=datetime.now(UTC))
    assert a.content_hash == b.content_hash


def test_fetch_feed_parses_rss_items(monkeypatch) -> None:
    rss_xml = """<?xml version="1.0"?>
    <rss><channel>
        <item><title>Fed announces rate decision</title><link>https://fed.gov/1</link><pubDate>Mon, 03 Aug 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""

    import httpx

    def fake_get(url, timeout=20.0, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=rss_xml, request=request)

    monkeypatch.setattr("polymarketpulse.news.rss.httpx.get", fake_get)
    events = fetch_feed("https://fed.gov/feed", "federal_reserve")
    assert len(events) == 1
    assert events[0].title == "Fed announces rate decision"


def test_fetch_feed_returns_empty_on_malformed_xml(monkeypatch) -> None:
    import httpx

    def fake_get(url, timeout=20.0, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, text="not xml at all <<<", request=request)

    monkeypatch.setattr("polymarketpulse.news.rss.httpx.get", fake_get)
    events = fetch_feed("https://fed.gov/feed", "federal_reserve")
    assert events == []


def test_link_news_to_markets_requires_shared_terms() -> None:
    event = NewsEvent(
        source="fed",
        source_url="https://fed.gov/1",
        title="Federal Reserve announces rate decision",
        published_at=None,
        fetched_at=datetime.now(UTC),
    )
    matching_market = Market(
        provider="polymarket",
        provider_market_id="1",
        condition_id="",
        question="Will the Federal Reserve cut rates in September?",
        slug="fed-rate-cut",
    )
    unrelated_market = Market(
        provider="polymarket",
        provider_market_id="2",
        condition_id="",
        question="Will it rain in Paris tomorrow?",
        slug="paris-rain",
    )
    links = link_news_to_markets([event], [matching_market, unrelated_market])
    linked_market_ids = {link.market.provider_market_id for link in links}
    assert "1" in linked_market_ids
    assert "2" not in linked_market_ids
    for link in links:
        assert link.confidence > 0
        assert link.matched_terms
