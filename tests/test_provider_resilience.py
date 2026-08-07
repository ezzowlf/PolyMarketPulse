"""Provider-failure resilience: a single market/provider failure must not
abort a batch scan of many markets, and known provider failures should be
reflected in data quality rather than silently ignored or crashing."""

from __future__ import annotations

import httpx
import pytest

from polymarketpulse.ai.client import AIContextError
from polymarketpulse.ai.service import get_prediction
from polymarketpulse.models import Market
from polymarketpulse.news.gdelt import fetch_gdelt
from polymarketpulse.news.rss import fetch_feed
from polymarketpulse.providers.polymarket_flow import fetch_order_book
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


def test_gdelt_provider_failure_returns_empty_not_exception(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.news.gdelt.assert_safe_url", lambda url: None)
    with pytest.MonkeyPatch().context() as m:
        m.setattr("polymarketpulse.news.gdelt.httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("boom")))
        events = fetch_gdelt("test query")
    assert events == []


def test_rss_provider_failure_returns_empty_not_exception(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.news.rss.assert_safe_url", lambda url: None)

    def _raise(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("polymarketpulse.news.rss.httpx.get", _raise)
    events = fetch_feed("https://example.gov/feed", "test_source")
    assert events == []


def test_order_book_provider_failure_returns_unfetched_not_exception(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.assert_safe_url", lambda url: None)
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.RETRY_BACKOFF_SECONDS", 0.0)

    def _raise(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.httpx.get", _raise)
    result = fetch_order_book("123")
    assert result.fetched is False


def test_one_market_lookup_failure_does_not_abort_batch(tmp_path) -> None:
    """Simulates the pattern used by cli.py's shadow-scan and
    opportunities.list_opportunities: iterate markets, catch AIContextError
    per-market, keep going. A missing/broken market must not stop the rest
    of a real batch scan from completing."""
    storage = Storage(tmp_path / "test.db")
    market = Market(
        provider="polymarket", provider_market_id="1", condition_id="", question="Will X happen?",
        slug="m-1", category="esports", liquidity=5000, volume_24h=100, yes_price=0.5,
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    real_market_id = storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = '1'"
    ).fetchone()[0]

    market_ids = ["does-not-exist-1", real_market_id, "does-not-exist-2"]
    results = []
    for mid in market_ids:
        try:
            results.append(get_prediction(storage, mid))
        except AIContextError:
            results.append(None)

    assert results[0] is None
    assert results[1] is not None
    assert results[2] is None
    storage.close()
