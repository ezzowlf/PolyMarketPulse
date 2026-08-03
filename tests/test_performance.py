from pathlib import Path

import pytest

from polymarketpulse.performance import compute_performance
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _insert_evaluation(storage: Storage, evaluated_at: str, pnl: float, correct: int | None, hold: float) -> None:
    storage.connection.execute(
        """
        INSERT INTO research_signals (run_id, provider, provider_market_id, captured_at, signal_type,
                                       score, reasons, subfactors_json, status)
        VALUES (NULL, 'polymarket', 'x', ?, 'NEW_MARKET', 50, '', '{}', 'resolved')
        """,
        (evaluated_at,),
    )
    signal_id = storage.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    storage.connection.execute(
        """
        INSERT INTO market_resolutions (provider, provider_market_id, resolved_at, status, detected_at)
        VALUES ('polymarket', 'x', ?, 'resolved', ?)
        ON CONFLICT(provider, provider_market_id) DO NOTHING
        """,
        (evaluated_at, evaluated_at),
    )
    resolution_id = storage.connection.execute(
        "SELECT id FROM market_resolutions WHERE provider='polymarket' AND provider_market_id='x'"
    ).fetchone()[0]
    storage.connection.execute(
        """
        INSERT INTO signal_evaluations (signal_id, resolution_id, simulated_pnl_per_unit, correct,
                                         hold_duration_hours, evaluated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (signal_id, resolution_id, pnl, correct, hold, evaluated_at),
    )
    storage.connection.commit()


def test_no_evaluations_returns_empty_summary(storage: Storage) -> None:
    summary = compute_performance(storage.connection)
    assert summary.evaluated_count == 0
    assert summary.cumulative_return is None


def test_cumulative_return_sums_pnl(storage: Storage) -> None:
    _insert_evaluation(storage, "2026-01-01T00:00:00", 0.4, 1, 10)
    _insert_evaluation(storage, "2026-01-02T00:00:00", -0.2, 0, 5)
    summary = compute_performance(storage.connection)
    assert summary.evaluated_count == 2
    assert round(summary.cumulative_return, 2) == 0.2
    assert summary.win_rate == 0.5


def test_max_drawdown_tracks_equity_dip() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        storage = Storage(Path(d) / "dd.db")
        _insert_evaluation(storage, "2026-01-01T00:00:00", 1.0, 1, 1)
        _insert_evaluation(storage, "2026-01-02T00:00:00", -0.8, 0, 1)
        _insert_evaluation(storage, "2026-01-03T00:00:00", 0.3, 1, 1)
        summary = compute_performance(storage.connection)
        assert summary.max_drawdown is not None
        assert round(summary.max_drawdown, 2) == 0.8
        storage.close()
