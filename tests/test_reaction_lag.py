from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.prediction.reaction_lag import (
    STATUS_NOT_YET_REACTED,
    STATUS_REACTED,
    compute_market_reaction_lag,
)
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _seed_snapshot(storage: Storage, provider_market_id: str, yes_price: float, captured_at: datetime) -> str:
    market = Market(
        provider="polymarket", provider_market_id=provider_market_id, condition_id="", question="Will X happen?",
        slug=f"m-{provider_market_id}", category="geopolitics", liquidity=50000, volume_24h=1000,
        yes_price=yes_price, start_at=captured_at,
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = ?",
        (provider_market_id,),
    ).fetchone()[0]
    storage.connection.execute(
        "UPDATE market_snapshots SET captured_at = ? WHERE market_id = ?",
        (captured_at.isoformat(), market_id),
    )
    storage.connection.commit()
    return market_id


def test_no_evidence_timestamp_returns_unknown_status(storage: Storage) -> None:
    market_id = _seed_snapshot(storage, "1", 0.5, datetime.now(UTC))
    result = compute_market_reaction_lag(storage.connection, market_id, None)
    assert result.reaction_detected_at_hours is None


def test_reaction_detected_when_price_moves_after_evidence(storage: Storage) -> None:
    now = datetime.now(UTC)
    market_id = _seed_snapshot(storage, "1", 0.5, now - timedelta(hours=5))
    run_id = storage.start_run("polymarket")
    storage.connection.execute(
        "INSERT INTO market_snapshots (run_id, market_id, captured_at, yes_price, no_price, liquidity, "
        "volume_24h, volume_total, opportunity_score, reasons) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, market_id, (now - timedelta(hours=2)).isoformat(), 0.65, 0.35, 50000, 1000, 1000, 0, "[]"),
    )
    storage.connection.commit()

    result = compute_market_reaction_lag(storage.connection, market_id, now - timedelta(hours=5), now=now)
    assert result.status == STATUS_REACTED
    assert result.reaction_detected_at_hours is not None
    assert result.reaction_detected_at_hours > 0


def test_no_reaction_when_price_stays_flat(storage: Storage) -> None:
    now = datetime.now(UTC)
    market_id = _seed_snapshot(storage, "1", 0.5, now - timedelta(hours=5))
    result = compute_market_reaction_lag(storage.connection, market_id, now - timedelta(hours=5), now=now)
    assert result.status == STATUS_NOT_YET_REACTED
