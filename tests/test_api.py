from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from polymarketpulse.models import Market
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("POLYMARKETPULSE_DATABASE_PATH", str(tmp_path / "api_test.db"))
    monkeypatch.setenv("POLYMARKETPULSE_TELEGRAM_ENABLED", "false")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    from polymarketpulse.api import app
    from polymarketpulse.config import Settings

    settings = Settings.load()
    storage = Storage(settings.database_path)
    market = Market(
        provider="polymarket",
        provider_market_id="1",
        condition_id="0xabc",
        question="Will it happen?",
        slug="will-it-happen",
        liquidity=50000,
        volume_24h=20000,
        yes_price=0.6,
        spread=0.02,
        end_at=datetime.now(UTC) + timedelta(days=5),
        start_at=datetime.now(UTC) - timedelta(hours=1),
        url="https://polymarket.com/event/will-it-happen",
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    from polymarketpulse.data_quality import assess_market

    storage.save_quality_reports(run_id, [("polymarket", assess_market(market))])
    storage.close()

    return TestClient(app)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_providers_lists_all(client: TestClient) -> None:
    resp = client.get("/providers")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()}
    assert "polymarket" in names


def test_provider_detail(client: TestClient) -> None:
    resp = client.get("/provider/polymarket")
    assert resp.status_code == 200
    assert resp.json()["name"] == "polymarket"


def test_provider_detail_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/provider/doesnotexist")
    assert resp.status_code == 404


def test_markets_returns_seeded_market(client: TestClient) -> None:
    resp = client.get("/markets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["question"] == "Will it happen?"


def test_markets_filters_by_search(client: TestClient) -> None:
    resp = client.get("/markets", params={"search": "nonexistent-term"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_market_detail_includes_signals(client: TestClient) -> None:
    listing = client.get("/markets").json()
    market_id = listing["items"][0]["market_id"]
    resp = client.get(f"/market/{market_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "Will it happen?"
    assert "signals" in body
    assert "news" in body


def test_market_detail_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/market/does-not-exist")
    assert resp.status_code == 404


def test_signals_endpoint(client: TestClient) -> None:
    resp = client.get("/signals")
    assert resp.status_code == 200
    assert len(resp.json()) > 0


def test_signal_detail(client: TestClient) -> None:
    signal_id = client.get("/signals").json()[0]["id"]
    resp = client.get(f"/signal/{signal_id}")
    assert resp.status_code == 200
    assert "subfactors" in resp.json()


def test_signal_detail_unknown_returns_404(client: TestClient) -> None:
    resp = client.get("/signal/999999")
    assert resp.status_code == 404


def test_stats_endpoint_returns_json(client: TestClient) -> None:
    resp = client.get("/stats")
    assert resp.status_code == 200
    assert "signal_count" in resp.json()


def test_history_endpoint(client: TestClient) -> None:
    market_id = client.get("/markets").json()["items"][0]["market_id"]
    resp = client.get(f"/history/{market_id}")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_watchlist_crud(client: TestClient) -> None:
    resp = client.post("/watchlist", json={"provider": "polymarket", "provider_market_id": "1", "note": "test"})
    assert resp.status_code == 200
    item_id = resp.json()["id"]

    listed = client.get("/watchlist").json()
    assert any(i["id"] == item_id for i in listed)

    deleted = client.delete(f"/watchlist/{item_id}")
    assert deleted.status_code == 200

    missing = client.delete(f"/watchlist/{item_id}")
    assert missing.status_code == 404


def test_watchlist_requires_provider_and_market_id(client: TestClient) -> None:
    resp = client.post("/watchlist", json={"note": "missing fields"})
    assert resp.status_code == 422


def test_calendar_endpoint(client: TestClient) -> None:
    resp = client.get("/calendar")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_heatmap_endpoint(client: TestClient) -> None:
    resp = client.get("/heatmap")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_analytics_endpoint(client: TestClient) -> None:
    resp = client.get("/analytics")
    assert resp.status_code == 200
    assert "schema_version" in resp.json()


def test_settings_endpoint_never_exposes_telegram_token(client: TestClient) -> None:
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "telegram_bot_token" not in body
    assert "TELEGRAM_BOT_TOKEN" not in str(body)


def test_news_endpoint_empty_by_default(client: TestClient) -> None:
    resp = client.get("/news")
    assert resp.status_code == 200
    assert resp.json() == []


def test_no_order_or_wallet_endpoints_exist(client: TestClient) -> None:
    from polymarketpulse.api import app

    paths = {route.path for route in app.routes}
    forbidden_terms = ("order", "wallet", "trade", "buy", "sell", "withdraw", "deposit")
    for path in paths:
        for term in forbidden_terms:
            assert term not in path.lower(), f"Unexpected trading-related route: {path}"


def test_quality_endpoint_returns_reports(client: TestClient) -> None:
    resp = client.get("/quality")
    assert resp.status_code == 200
    reports = resp.json()
    assert len(reports) == 1
    assert "score" in reports[0]


def test_performance_endpoint_on_empty_evaluations(client: TestClient) -> None:
    resp = client.get("/performance")
    assert resp.status_code == 200
    assert resp.json()["evaluated_count"] == 0


def test_simulation_endpoint_returns_list(client: TestClient) -> None:
    resp = client.get("/simulation")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_resolutions_endpoint_returns_list(client: TestClient) -> None:
    resp = client.get("/resolutions")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_providers_status_endpoint(client: TestClient) -> None:
    resp = client.get("/providers/status")
    assert resp.status_code == 200
    body = resp.json()
    names = {p["name"] for p in body}
    assert "polymarket" in names
    poly = next(p for p in body if p["name"] == "polymarket")
    assert poly["markets_stored"] == 1


def test_search_endpoint_finds_seeded_market(client: TestClient) -> None:
    resp = client.get("/search", params={"q": "happen"})
    assert resp.status_code == 200
    assert len(resp.json()["markets"]) == 1


def test_search_endpoint_rejects_short_query(client: TestClient) -> None:
    resp = client.get("/search", params={"q": "a"})
    assert resp.status_code == 422


def test_compare_endpoint_empty_with_single_provider(client: TestClient) -> None:
    resp = client.get("/compare")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_full_endpoint_includes_analytics(client: TestClient) -> None:
    market_id = client.get("/markets").json()["items"][0]["market_id"]
    resp = client.get(f"/history/full/{market_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "points" in body
    assert "analytics" in body
    assert "volatility" in body["analytics"]


def test_explain_endpoint_returns_grounded_statements(client: TestClient) -> None:
    market_id = client.get("/markets").json()["items"][0]["market_id"]
    resp = client.get(f"/explain/{market_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "statements" in body
    assert len(body["statements"]) > 0


def test_analytics_endpoint_includes_signal_stats(client: TestClient) -> None:
    resp = client.get("/analytics")
    assert resp.status_code == 200
    assert "signal_stats" in resp.json()


def test_watchlist_supports_tags_rating_group(client: TestClient) -> None:
    resp = client.post(
        "/watchlist",
        json={
            "provider": "polymarket",
            "provider_market_id": "1",
            "tags": ["macro", "fed"],
            "rating": 5,
            "group": "watch-closely",
        },
    )
    assert resp.status_code == 200
    item = client.get("/watchlist").json()[0]
    assert item["tags"] == ["macro", "fed"]
    assert item["rating"] == 5
    assert item["group"] == "watch-closely"
