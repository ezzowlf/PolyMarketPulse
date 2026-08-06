from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.opportunities import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_PRICE_MISSING,
    list_opportunities,
)
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _seed_market(storage: Storage, provider_market_id="1", yes_price=0.5, category="esports") -> str:
    market = Market(
        provider="polymarket", provider_market_id=provider_market_id, condition_id="", question="Will X happen?",
        slug=f"market-{provider_market_id}", category=category, liquidity=100000, volume_24h=20000, yes_price=yes_price,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = ?",
        (provider_market_id,),
    ).fetchone()[0]


def test_list_opportunities_empty_db_returns_empty_list(storage: Storage) -> None:
    assert list_opportunities(storage) == []


def test_market_with_price_and_no_history_is_insufficient_data(storage: Storage) -> None:
    _seed_market(storage)
    items = list_opportunities(storage)
    assert len(items) == 1
    assert items[0]["status"] == STATUS_INSUFFICIENT_DATA
    assert items[0]["market_yes_probability"] == 0.5


def test_market_without_price_is_flagged_price_missing(storage: Storage) -> None:
    market = Market(
        provider="polymarket", provider_market_id="no-price", condition_id="", question="No price market",
        slug="no-price", category="esports", liquidity=0, volume_24h=0, yes_price=None,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    items = list_opportunities(storage)
    assert len(items) == 1
    assert items[0]["status"] == STATUS_PRICE_MISSING
    assert items[0]["market_yes_probability"] is None


def test_opportunity_score_never_negative_or_above_100(storage: Storage) -> None:
    _seed_market(storage)
    items = list_opportunities(storage)
    assert 0.0 <= items[0]["opportunity_score"] <= 100.0


def test_change_since_last_analysis_none_on_first_run(storage: Storage) -> None:
    _seed_market(storage)
    items = list_opportunities(storage)
    assert items[0]["change_since_last_analysis"] is None


def test_change_since_last_analysis_present_on_second_run(storage: Storage) -> None:
    _seed_market(storage)
    list_opportunities(storage)  # first run persists a snapshot
    items = list_opportunities(storage)  # second run compares to it
    assert items[0]["change_since_last_analysis"] is not None
    change = items[0]["change_since_last_analysis"]
    assert "market_yes_probability" in change
    assert set(change["market_yes_probability"].keys()) == {"from", "to"}


def test_deadline_bucket_labels() -> None:
    from polymarketpulse.opportunities import deadline_bucket

    assert deadline_bucket(None) == "unbekannt"
    assert deadline_bucket(-5) == "abgelaufen"
    assert deadline_bucket(12) == "<24h"
    assert deadline_bucket(48) == "1-3 Tage"
    assert deadline_bucket(96) == "3-7 Tage"
    assert deadline_bucket(300) == ">7 Tage"


def test_resolved_markets_excluded_from_opportunities(storage: Storage) -> None:
    _seed_market(storage, provider_market_id="resolved-1")
    storage.connection.execute("UPDATE markets SET resolution_status = 'resolved'")
    storage.connection.commit()
    assert list_opportunities(storage) == []
