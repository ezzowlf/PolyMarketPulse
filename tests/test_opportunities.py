from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.opportunities import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_PRICE_MISSING,
    compute_opportunity,
    list_opportunities,
    list_ranked_opportunities,
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


def _seed_market_row(storage: Storage, provider_market_id="1", yes_price=0.5, category="esports") -> dict:
    """Block E Part 2: a bare, no-evidence market never gets a real
    `published_forecast_probability`, so it correctly no longer appears in
    `list_opportunities` at all (see test_bare_market_excluded_from_ranked_
    opportunities below). These per-field assertions moved to
    `compute_opportunity` directly, which still returns a labeled entry for
    single-market detail views (api.py) regardless of ranking eligibility."""
    market_id = _seed_market(storage, provider_market_id=provider_market_id, yes_price=yes_price, category=category)
    cols = ("market_id", "provider", "provider_market_id", "question", "category", "url",
            "end_date", "first_seen_at", "last_seen_at")
    row = storage.connection.execute(
        f"SELECT {', '.join(cols)} FROM markets WHERE market_id = ?", (market_id,),
    ).fetchone()
    return dict(zip(cols, row, strict=True))


def test_bare_market_excluded_from_ranked_opportunities(storage: Storage) -> None:
    """Block E Part 2: a market with no evidence/history has
    published_forecast_probability=None, so it must NEVER appear in the
    ranked opportunities list, even though it still gets a labeled
    single-market view via compute_opportunity."""
    _seed_market(storage)
    assert list_ranked_opportunities(storage) == []


def test_market_with_price_and_no_history_is_insufficient_data(storage: Storage) -> None:
    row = _seed_market_row(storage)
    opp = compute_opportunity(storage, row)
    assert opp is not None
    assert opp["status"] == STATUS_INSUFFICIENT_DATA
    assert opp["market_yes_probability"] == 0.5
    assert opp["is_ranked_opportunity"] is False
    assert opp["published_forecast_probability"] is None


def test_market_without_price_is_flagged_price_missing(storage: Storage) -> None:
    market = Market(
        provider="polymarket", provider_market_id="no-price", condition_id="", question="No price market",
        slug="no-price", category="esports", liquidity=0, volume_24h=0, yes_price=None,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    row = _seed_market_row(storage, provider_market_id="no-price", yes_price=None)

    opp = compute_opportunity(storage, row)
    assert opp is not None
    assert opp["status"] == STATUS_PRICE_MISSING
    assert opp["market_yes_probability"] is None
    assert opp["is_ranked_opportunity"] is False


def test_opportunity_score_never_negative_or_above_100(storage: Storage) -> None:
    row = _seed_market_row(storage)
    opp = compute_opportunity(storage, row)
    assert opp is not None
    assert 0.0 <= opp["opportunity_score"] <= 100.0


def test_change_since_last_analysis_none_on_first_run(storage: Storage) -> None:
    row = _seed_market_row(storage)
    opp = compute_opportunity(storage, row)
    assert opp is not None
    assert opp["change_since_last_analysis"] is None


def test_change_since_last_analysis_present_on_second_run(storage: Storage) -> None:
    row = _seed_market_row(storage)
    compute_opportunity(storage, row)  # first run persists a snapshot
    opp = compute_opportunity(storage, row)  # second run compares to it
    assert opp is not None
    assert opp["change_since_last_analysis"] is not None
    change = opp["change_since_last_analysis"]
    assert "market_yes_probability" in change
    assert set(change["market_yes_probability"].keys()) == {"from", "to"}
    assert change["published_forecast_probability"]["from"] is None
    assert change["published_forecast_probability"]["to"] is None


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
