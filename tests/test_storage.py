from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market, ResolutionStatus
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
        "condition_id": "0xabc",
        "question": "Test market",
        "slug": "test-market",
        "liquidity": 50000,
        "volume_24h": 20000,
        "volume_total": 200000,
        "spread": 0.03,
        "yes_price": 0.5,
        "url": "https://polymarket.com/event/test-market",
        "start_at": datetime.now(UTC) - timedelta(hours=1),
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_save_upserts_markets_without_duplicating(storage: Storage) -> None:
    market = _market()
    signals = generate_signals(market)
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, signals)])

    run_id_2 = storage.start_run("polymarket")
    storage.save(run_id_2, [(market, signals)])

    count = storage.connection.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    assert count == 1


def test_save_skips_unchanged_snapshot_by_default(storage: Storage) -> None:
    market = _market()
    signals = generate_signals(market)
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, signals)])

    run_id_2 = storage.start_run("polymarket")
    written = storage.save(run_id_2, [(market, signals)])

    assert written == 0
    snapshot_count = storage.connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert snapshot_count == 1


def test_save_writes_new_snapshot_when_price_changes(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    changed = _market(yes_price=0.65)
    run_id_2 = storage.start_run("polymarket")
    written = storage.save(run_id_2, [(changed, generate_signals(changed))])

    assert written == 1
    snapshot_count = storage.connection.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert snapshot_count == 2


def test_store_unchanged_snapshots_when_configured(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "test.db", store_unchanged_snapshots=True)
    try:
        market = _market()
        run_id = storage.start_run("polymarket")
        storage.save(run_id, [(market, generate_signals(market))])
        run_id_2 = storage.start_run("polymarket")
        written = storage.save(run_id_2, [(market, generate_signals(market))])
        assert written == 1
    finally:
        storage.close()


def test_record_resolution_is_idempotent(storage: Storage) -> None:
    market = _market(
        resolution_status=ResolutionStatus.RESOLVED,
        winning_outcome="Yes",
        resolved_at=datetime.now(UTC),
    )
    first = storage.record_resolution(market)
    second = storage.record_resolution(market)
    assert first is True
    assert second is False
    count = storage.connection.execute("SELECT COUNT(*) FROM market_resolutions").fetchone()[0]
    assert count == 1


def test_resolution_evaluates_open_signals(storage: Storage) -> None:
    market = _market(yes_price=0.7)
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    resolved = _market(
        yes_price=1.0,
        resolution_status=ResolutionStatus.RESOLVED,
        winning_outcome="Yes",
        resolved_at=datetime.now(UTC) + timedelta(days=1),
    )
    storage.record_resolution(resolved)

    row = storage.connection.execute(
        "SELECT correct FROM signal_evaluations LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == 1


def test_save_upserts_legacy_unprefixed_market_id_without_collision(storage: Storage) -> None:
    # Simulate a pre-Phase-2 row whose market_id has no provider prefix
    # (exactly what migration 002 backfills existing Phase-1 rows to).
    now = datetime.now(UTC).isoformat()
    storage.connection.execute(
        """
        INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url,
                              first_seen_at, last_seen_at, resolution_status)
        VALUES ('legacy123', 'polymarket', 'legacy123', 'Legacy market', 'legacy', 'https://x',
                ?, ?, 'unresolved')
        """,
        (now, now),
    )
    storage.connection.commit()

    market = _market(provider="polymarket", provider_market_id="legacy123", question="Updated question")
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    rows = storage.connection.execute(
        "SELECT market_id, question FROM markets WHERE provider = 'polymarket' AND provider_market_id = 'legacy123'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "legacy123"
    assert rows[0][1] == "Updated question"


def test_provider_key_scoping_avoids_cross_provider_collision(storage: Storage) -> None:
    poly_market = _market(provider="polymarket", provider_market_id="42")
    manifold_market = _market(provider="manifold", provider_market_id="42")
    run_id = storage.start_run("polymarket")
    storage.save(
        run_id,
        [
            (poly_market, generate_signals(poly_market)),
            (manifold_market, generate_signals(manifold_market)),
        ],
    )
    count = storage.connection.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    assert count == 2
