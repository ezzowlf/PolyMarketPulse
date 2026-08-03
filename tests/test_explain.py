from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.explain import (
    explain_market_movement,
    relevant_news_for_market,
    signals_before_movement,
    similar_markets,
)
from polymarketpulse.models import Market
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _market(**overrides) -> Market:
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "",
        "question": "Will the Fed cut rates?",
        "slug": "fed-cut",
        "liquidity": 50000,
        "volume_24h": 20000,
        "yes_price": 0.6,
        "start_at": datetime.now(UTC) - timedelta(hours=1),
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_explain_movement_unknown_market_reports_not_found(storage: Storage) -> None:
    explanation = explain_market_movement(storage.connection, "does-not-exist")
    assert "nicht gefunden" in explanation.statements[0]


def test_explain_movement_reports_price_change(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    changed = _market(yes_price=0.75)
    run_id_2 = storage.start_run("polymarket")
    storage.save(run_id_2, [(changed, generate_signals(changed))])

    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]
    explanation = explain_market_movement(storage.connection, market_id)
    assert any("YES-Preis änderte sich" in s for s in explanation.statements)


def test_signals_before_movement_lists_signals(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]

    explanation = signals_before_movement(storage.connection, market_id)
    assert explanation.statements != ["Keine vorherigen Signale gespeichert."]


def test_relevant_news_empty_when_none_linked(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]

    explanation = relevant_news_for_market(storage.connection, market_id)
    assert explanation.statements == ["Keine verknüpften News gefunden."]


def test_similar_markets_returns_none_when_no_resolved_markets(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]

    explanation = similar_markets(storage.connection, market_id)
    assert "Keine ausreichend ähnlichen" in explanation.statements[0]


def test_no_statement_claims_certainty_or_recommendation(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]

    explanation = explain_market_movement(storage.connection, market_id)
    forbidden = ("kaufen", "sicherer gewinn", "garantiert", "jetzt setzen")
    for statement in explanation.statements:
        lowered = statement.lower()
        for phrase in forbidden:
            assert phrase not in lowered
