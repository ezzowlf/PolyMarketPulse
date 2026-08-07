from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.prediction.cross_market import compute_cross_market_relations
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _seed(storage: Storage, provider_market_id: str, question: str, yes_price: float) -> str:
    market = Market(
        provider="polymarket", provider_market_id=provider_market_id, condition_id="", question=question,
        slug=f"m-{provider_market_id}", category="geopolitics", liquidity=50000, volume_24h=1000,
        yes_price=yes_price, start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = ?",
        (provider_market_id,),
    ).fetchone()[0]


def test_no_related_markets_returns_unavailable(storage: Storage) -> None:
    market_id = _seed(storage, "1", "Will the ceasefire agreement be signed?", 0.5)
    result = compute_cross_market_relations(storage.connection, market_id, "polymarket", "Will the ceasefire agreement be signed?", 0.5)
    assert result.available is False


def test_strongly_related_markets_with_diverging_prices_flagged_inconsistent(storage: Storage) -> None:
    market_id = _seed(storage, "1", "Will the ceasefire agreement be officially signed by both parties?", 0.9)
    _seed(storage, "2", "Will the ceasefire agreement be officially signed by both parties this year?", 0.3)

    result = compute_cross_market_relations(
        storage.connection, market_id, "polymarket",
        "Will the ceasefire agreement be officially signed by both parties?", 0.9,
    )
    assert result.available is True
    assert len(result.related_markets) >= 1
    assert result.max_divergence is not None
    assert result.max_divergence > 0.3
    assert result.logical_inconsistency_score is not None
    assert result.logical_inconsistency_score > 0


def test_unrelated_markets_are_not_matched(storage: Storage) -> None:
    market_id = _seed(storage, "1", "Will the ceasefire agreement be signed?", 0.5)
    _seed(storage, "2", "Will the stock market crash next quarter?", 0.2)

    result = compute_cross_market_relations(storage.connection, market_id, "polymarket", "Will the ceasefire agreement be signed?", 0.5)
    assert result.available is False
