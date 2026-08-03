from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from polymarketpulse.models import Market
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def seeded_market_id(tmp_path: Path, monkeypatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("POLYMARKETPULSE_DATABASE_PATH", str(tmp_path / "api_ai_test.db"))
    monkeypatch.setenv("POLYMARKETPULSE_TELEGRAM_ENABLED", "false")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "false")

    from polymarketpulse.api import app
    from polymarketpulse.config import Settings

    settings = Settings.load()
    storage = Storage(settings.database_path)
    market = Market(
        provider="polymarket",
        provider_market_id="1",
        condition_id="",
        question="Will it happen?",
        slug="will-it-happen",
        liquidity=50000,
        volume_24h=20000,
        yes_price=0.6,
        start_at=datetime.now(UTC) - timedelta(hours=1),
        url="https://polymarket.com/event/will-it-happen",
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]
    storage.close()

    return TestClient(app), market_id


def test_ai_status_shows_disabled_by_default(seeded_market_id) -> None:
    client, _ = seeded_market_id
    resp = client.get("/ai/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["ready"] is False
    assert body["reason"]


def test_ai_status_never_exposes_api_key(seeded_market_id) -> None:
    client, _ = seeded_market_id
    resp = client.get("/ai/status")
    assert "OPENAI_API_KEY" not in str(resp.json())
    assert "sk-" not in str(resp.json())


def test_explain_market_returns_503_when_disabled(seeded_market_id) -> None:
    client, market_id = seeded_market_id
    resp = client.post(f"/ai/explain-market/{market_id}")
    assert resp.status_code == 503
    assert "OPENAI_API_KEY" not in resp.text


def test_explain_signal_returns_503_when_disabled(seeded_market_id) -> None:
    client, _ = seeded_market_id
    resp = client.post("/ai/explain-signal/1")
    assert resp.status_code == 503


def test_analyze_news_returns_503_when_disabled(seeded_market_id) -> None:
    client, market_id = seeded_market_id
    resp = client.post(f"/ai/analyze-news/{market_id}")
    assert resp.status_code == 503


def test_compare_returns_503_when_disabled(seeded_market_id) -> None:
    client, market_id = seeded_market_id
    resp = client.post("/ai/compare", json={"market_ids": [market_id, "other"]})
    assert resp.status_code == 503


def test_ask_returns_503_when_disabled(seeded_market_id) -> None:
    client, _ = seeded_market_id
    resp = client.post("/ai/ask", json={"question": "Why did this move?"})
    assert resp.status_code == 503


def test_ask_rejects_too_short_question(seeded_market_id) -> None:
    client, _ = seeded_market_id
    resp = client.post("/ai/ask", json={"question": "?"})
    assert resp.status_code == 422


def test_compare_rejects_single_market(seeded_market_id) -> None:
    client, market_id = seeded_market_id
    resp = client.post("/ai/compare", json={"market_ids": [market_id]})
    assert resp.status_code == 422


def test_explain_market_with_ai_enabled_but_no_key_returns_503(seeded_market_id, monkeypatch) -> None:
    client, market_id = seeded_market_id
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "true")
    resp = client.post(f"/ai/explain-market/{market_id}")
    assert resp.status_code == 503


def test_explain_market_unknown_market_returns_424_when_ai_would_be_ready(
    seeded_market_id, monkeypatch
) -> None:
    client, _ = seeded_market_id
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-not-a-real-key")
    resp = client.post("/ai/explain-market/does-not-exist")
    assert resp.status_code == 424


def test_no_wallet_or_order_routes_added_by_ai_module(seeded_market_id) -> None:
    from polymarketpulse.api import app

    paths = {route.path for route in app.routes}
    forbidden = ("wallet", "order", "trade", "withdraw", "deposit")
    for path in paths:
        for term in forbidden:
            assert term not in path.lower()
