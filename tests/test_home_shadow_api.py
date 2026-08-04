from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from polymarketpulse.models import Market
from polymarketpulse.shadow import evaluate_shadow_setup
from polymarketpulse.signals import PreviousSnapshot, generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def client_with_shadow_setup(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("POLYMARKETPULSE_DATABASE_PATH", str(tmp_path / "home_test.db"))
    monkeypatch.setenv("POLYMARKETPULSE_TELEGRAM_ENABLED", "false")

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
        liquidity=200000,
        volume_24h=50000,
        yes_price=0.6,
        end_at=datetime.now(UTC) + timedelta(days=3),
        url="https://polymarket.com/event/will-it-happen",
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    previous = PreviousSnapshot(liquidity=200000, volume_24h=10000, spread=0.02, yes_price=0.45, one_day_change=0.1)
    setup = evaluate_shadow_setup(market, previous=previous, data_quality_score=95)
    storage.save_shadow_setup(run_id, setup)
    storage.close()

    return TestClient(app)


def test_home_endpoint_returns_daily_summary(client_with_shadow_setup: TestClient) -> None:
    resp = client_with_shadow_setup.get("/home")
    assert resp.status_code == 200
    body = resp.json()
    assert "heute" in body
    assert set(body["heute"].keys()) == {
        "maerkte_mit_hoher_aufmerksamkeit",
        "neue_shadow_setups",
        "wichtige_nachrichten",
        "maerkte_vor_entscheidung",
    }


def test_home_highlights_max_five(client_with_shadow_setup: TestClient) -> None:
    resp = client_with_shadow_setup.get("/home")
    body = resp.json()
    assert len(body["besonders_interessant"]) <= 5


def test_home_highlight_has_required_fields(client_with_shadow_setup: TestClient) -> None:
    resp = client_with_shadow_setup.get("/home")
    body = resp.json()
    assert len(body["besonders_interessant"]) == 1
    highlight = body["besonders_interessant"][0]
    for field in (
        "frage",
        "aktueller_preis",
        "veraenderung_seit_erkennung",
        "research_score",
        "shadow_score",
        "wichtigste_gruende",
        "wichtigste_risiken",
        "tage_bis_resolution",
    ):
        assert field in highlight


def test_shadow_setups_list_endpoint(client_with_shadow_setup: TestClient) -> None:
    resp = client_with_shadow_setup.get("/shadow-setups")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "aktiv"
    assert "breakdown" in body[0]
    assert "warum_interessant" in body[0]


def test_shadow_setups_filter_by_status(client_with_shadow_setup: TestClient) -> None:
    resp = client_with_shadow_setup.get("/shadow-setups", params={"status": "aufgelöst"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_shadow_setup_detail_endpoint(client_with_shadow_setup: TestClient) -> None:
    list_resp = client_with_shadow_setup.get("/shadow-setups")
    setup_id = list_resp.json()[0]["id"]
    resp = client_with_shadow_setup.get(f"/shadow-setup/{setup_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "preisverlauf_seit_erkennung" in body
    assert "nachrichten_seitdem" in body


def test_shadow_setup_detail_404_for_unknown_id(client_with_shadow_setup: TestClient) -> None:
    resp = client_with_shadow_setup.get("/shadow-setup/999999")
    assert resp.status_code == 404


def test_home_never_exposes_secrets(client_with_shadow_setup: TestClient) -> None:
    resp = client_with_shadow_setup.get("/home")
    assert "OPENAI_API_KEY" not in resp.text
    assert "sk-" not in resp.text
