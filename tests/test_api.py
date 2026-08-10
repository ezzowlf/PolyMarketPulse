from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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
    # Never let automated tests make a real OpenAI call — a real key may be
    # present in the local .env from a previous manual live-smoke-test.
    # Settings.load() reads .env directly, so an unset env var isn't enough
    # on its own; force both flags explicitly.
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")

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


def test_data_gaps_endpoint_uses_canonical_prediction_report(
    client: TestClient, monkeypatch,
) -> None:
    from polymarketpulse import api

    market_id = client.get("/markets").json()["items"][0]["market_id"]
    canonical = {
        "market_id": market_id,
        "gaps": [{"category": "EVENT_GRAPH", "severity": "LOW"}],
    }
    monkeypatch.setattr(
        api.ai_service,
        "get_prediction",
        lambda storage, requested_id: SimpleNamespace(
            data_gaps=SimpleNamespace(as_dict=lambda: canonical)
        ),
    )

    response = client.get(f"/data-gaps/{market_id}")

    assert response.status_code == 200
    assert response.json()["market_id"] == market_id
    assert response.json()["gaps"] == canonical["gaps"]


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


def _seeded_market_id(client: TestClient) -> str:
    return client.get("/markets").json()["items"][0]["market_id"]


def test_prediction_endpoint_returns_binding_values(client: TestClient) -> None:
    market_id = _seeded_market_id(client)
    resp = client.get(f"/prediction/{market_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["market_id"] == market_id
    assert data["recommendation"] in (
        "STRONG_YES", "YES", "WATCH_YES", "NO_BET", "WATCH_NO", "NO", "STRONG_NO", "INSUFFICIENT_DATA",
    )


def test_prediction_endpoint_unknown_market_returns_424(client: TestClient) -> None:
    resp = client.get("/prediction/does-not-exist")
    assert resp.status_code == 424


def test_ai_explain_recommendation_falls_back_without_api_key(client: TestClient) -> None:
    market_id = _seeded_market_id(client)
    resp = client.get(f"/ai/explain-recommendation/{market_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["used_fallback"] is True
    assert data["explanation"]["summary"]
    assert data["explanation"]["recommendation"] == data["prediction"]["recommendation"]


def test_ai_explain_recommendation_recompute_endpoint(client: TestClient) -> None:
    market_id = _seeded_market_id(client)
    resp = client.post(f"/ai/explain-recommendation/{market_id}/recompute")
    assert resp.status_code == 200
    assert resp.json()["meta"]["used_fallback"] is True


def test_ai_cost_report_endpoint(client: TestClient) -> None:
    market_id = _seeded_market_id(client)
    client.get(f"/ai/explain-recommendation/{market_id}")
    resp = client.get("/ai/cost-report")
    assert resp.status_code == 200
    data = resp.json()
    assert "spent_today_usd" in data
    assert "by_model" in data


def test_command_center_endpoint(client: TestClient) -> None:
    resp = client.get("/command-center")
    assert resp.status_code == 200
    data = resp.json()
    assert "uebersicht" in data
    assert "interessanteste_maerkte" in data
    assert data["uebersicht"]["aktive_maerkte"] >= 1


def test_opportunities_endpoint(client: TestClient) -> None:
    resp = client.get("/opportunities")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert "status" in items[0]
    assert "opportunity_score" in items[0]


def test_opportunities_endpoint_filters_require_price(client: TestClient) -> None:
    resp = client.get("/opportunities?require_price=true")
    assert resp.status_code == 200
    for item in resp.json():
        assert item["market_yes_probability"] is not None


def test_watchlist_enriched_with_opportunity(client: TestClient) -> None:
    client.post("/watchlist", json={"provider": "polymarket", "provider_market_id": "1"})
    resp = client.get("/watchlist")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["opportunity"] is not None
    assert "status" in items[0]["opportunity"]


def test_market_detail_includes_opportunity(client: TestClient) -> None:
    market_id = _seeded_market_id(client)
    resp = client.get(f"/market/{market_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "opportunity" in data
    assert data["opportunity"]["market_id"] == market_id


def test_settings_endpoint_includes_ai_diagnostics_without_key(client: TestClient) -> None:
    resp = client.get("/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "ai" in data
    assert data["ai"]["enabled"] is False
    assert data["ai"]["api_key_present"] is False
    assert "editable_in_browser" in data
