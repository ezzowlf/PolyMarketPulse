import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from polymarketpulse.backtest import run_backtest
from polymarketpulse.migrations import run_migrations


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    run_migrations(c)
    yield c
    c.close()


def _insert_resolved_market(
    conn: sqlite3.Connection, market_id: str, category: str, resolved_at: str, outcome: str,
    snapshot_price: float, snapshot_at: str,
) -> None:
    conn.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
        "first_seen_at, last_seen_at, resolution_status, category) "
        "VALUES (?, 'polymarket', ?, 'x', 'x', 'https://x', ?, ?, 'resolved', ?)",
        (market_id, market_id, snapshot_at, resolved_at, category),
    )
    conn.execute(
        "INSERT INTO market_resolutions (provider, provider_market_id, resolved_at, winning_outcome, status, detected_at) "
        "VALUES ('polymarket', ?, ?, ?, 'resolved', ?)",
        (market_id, resolved_at, outcome, resolved_at),
    )
    conn.execute(
        "INSERT INTO scanner_runs (started_at, provider) VALUES (?, 'polymarket')",
        (snapshot_at,),
    )
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO market_snapshots (run_id, captured_at, market_id, yes_price, liquidity, "
        "volume_24h, volume_total, opportunity_score, reasons) "
        "VALUES (?, ?, ?, ?, 100000, 20000, 20000, 0, '[]')",
        (run_id, snapshot_at, market_id, snapshot_price),
    )
    conn.commit()


def test_backtest_on_empty_db_returns_no_cases(conn: sqlite3.Connection) -> None:
    report = run_backtest(conn)
    assert report.n_evaluated == 0
    assert report.brier_score is None
    assert report.cumulative_return == 0.0


def test_backtest_skips_cases_below_min_train_size(conn: sqlite3.Connection) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        _insert_resolved_market(
            conn, f"m-{i}", "esports", (base + timedelta(days=i)).isoformat(), "Yes",
            0.5, (base + timedelta(days=i) - timedelta(hours=1)).isoformat(),
        )
    report = run_backtest(conn, min_train_size=5)
    assert report.n_evaluated == 0
    assert report.n_skipped == 3
    assert report.skipped_reasons.get("zu_wenig_trainingsdaten") == 3


def test_backtest_never_uses_future_resolutions(conn: sqlite3.Connection) -> None:
    """The Nth case's implied prediction must only be derivable from the
    first N-1 resolved markets — verified indirectly: an all-YES history
    followed by a single NO case should predict close to observed 100% YES
    (i.e. it does NOT already 'know' the NO outcome that closes the set)."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(6):
        _insert_resolved_market(
            conn, f"m-yes-{i}", "esports", (base + timedelta(days=i)).isoformat(), "Yes",
            0.5, (base + timedelta(days=i) - timedelta(hours=1)).isoformat(),
        )
    _insert_resolved_market(
        conn, "m-no-final", "esports", (base + timedelta(days=6)).isoformat(), "No",
        0.5, (base + timedelta(days=6) - timedelta(hours=1)).isoformat(),
    )
    report = run_backtest(conn, min_train_size=5)
    assert report.n_evaluated == 2  # cases 6 and 7 (0-indexed) meet min_train_size=5
    last_case_predicted = None
    for c in report.cases:
        if c.market_id == "m-no-final":
            last_case_predicted = c.predicted_yes
    # Trained only on 6 prior all-YES cases -> predicted YES should be
    # pulled above the flat 0.5 market price (proving the engine used the
    # all-YES history), but the sample-size-capped blend weight (12% at
    # n=6) keeps it well short of the ~100% it would show if this case's
    # own NO outcome had leaked into training.
    assert last_case_predicted is not None
    assert 0.5 < last_case_predicted < 0.8


def test_backtest_computes_brier_and_log_loss(conn: sqlite3.Connection) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(10):
        outcome = "Yes" if i % 2 == 0 else "No"
        _insert_resolved_market(
            conn, f"m-{i}", "esports", (base + timedelta(days=i)).isoformat(), outcome,
            0.5, (base + timedelta(days=i) - timedelta(hours=1)).isoformat(),
        )
    report = run_backtest(conn, min_train_size=5)
    assert report.n_evaluated > 0
    assert report.brier_score is not None
    assert 0.0 <= report.brier_score <= 1.0
    assert report.log_loss is not None
    assert report.log_loss >= 0.0


def test_backtest_calibration_buckets_sum_to_evaluated_count(conn: sqlite3.Connection) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(10):
        outcome = "Yes" if i % 3 != 0 else "No"
        _insert_resolved_market(
            conn, f"m-{i}", "esports", (base + timedelta(days=i)).isoformat(), outcome,
            0.6, (base + timedelta(days=i) - timedelta(hours=1)).isoformat(),
        )
    report = run_backtest(conn, min_train_size=5)
    assert sum(b["n"] for b in report.calibration) == report.n_evaluated


def test_backtest_category_filter(conn: sqlite3.Connection) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(6):
        _insert_resolved_market(
            conn, f"esports-{i}", "esports", (base + timedelta(days=i)).isoformat(), "Yes",
            0.5, (base + timedelta(days=i) - timedelta(hours=1)).isoformat(),
        )
    for i in range(6):
        _insert_resolved_market(
            conn, f"politics-{i}", "politics", (base + timedelta(days=i)).isoformat(), "No",
            0.5, (base + timedelta(days=i) - timedelta(hours=1)).isoformat(),
        )
    report = run_backtest(conn, category="esports", min_train_size=5)
    assert all(c.category == "esports" for c in report.cases)
