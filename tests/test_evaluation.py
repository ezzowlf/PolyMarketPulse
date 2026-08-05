import sqlite3
from datetime import UTC, datetime

import pytest

from polymarketpulse.evaluation import evaluate_predictions
from polymarketpulse.migrations import run_migrations


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    run_migrations(c)
    yield c
    c.close()


def _seed_snapshot(
    conn, market_id, provider_market_id, estimated_yes, market_yes, net_edge, recommendation, category="esports"
):
    conn.execute(
        "INSERT INTO prediction_snapshots (market_id, provider, provider_market_id, category, "
        "prediction_version, created_at, market_yes_probability, estimated_yes_probability, net_yes_edge, "
        "confidence_score, recommendation, comparable_sample_size) "
        "VALUES (?, 'polymarket', ?, ?, 'v2', ?, ?, ?, ?, 60, ?, 10)",
        (market_id, provider_market_id, category, datetime.now(UTC).isoformat(), market_yes, estimated_yes, net_edge, recommendation),
    )
    conn.commit()


def _seed_resolution(conn, provider_market_id, outcome):
    conn.execute(
        "INSERT INTO market_resolutions (provider, provider_market_id, resolved_at, winning_outcome, status, detected_at) "
        "VALUES ('polymarket', ?, ?, ?, 'resolved', ?)",
        (provider_market_id, datetime.now(UTC).isoformat(), outcome, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def test_evaluation_on_empty_db(conn: sqlite3.Connection) -> None:
    report = evaluate_predictions(conn)
    assert report.n_snapshots_total == 0
    assert report.n_evaluable == 0
    assert report.accuracy is None
    assert report.brier_score is None


def test_evaluation_excludes_unresolved_markets(conn: sqlite3.Connection) -> None:
    _seed_snapshot(conn, "m1", "1", 0.7, 0.5, 0.18, "YES")
    report = evaluate_predictions(conn)
    assert report.n_snapshots_total == 1
    assert report.n_evaluable == 0


def test_evaluation_accuracy_precision_recall() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    # Correct YES call
    _seed_snapshot(conn, "m1", "1", 0.7, 0.5, 0.18, "YES")
    _seed_resolution(conn, "1", "Yes")
    # Incorrect YES call
    _seed_snapshot(conn, "m2", "2", 0.65, 0.5, 0.13, "YES")
    _seed_resolution(conn, "2", "No")
    # Correct NO call
    _seed_snapshot(conn, "m3", "3", 0.2, 0.5, -0.28, "NO")
    _seed_resolution(conn, "3", "No")

    report = evaluate_predictions(conn)
    conn.close()

    assert report.n_evaluable == 3
    assert report.n_directional == 3
    # 2 of 3 directional calls correct.
    assert report.accuracy == pytest.approx(2 / 3, abs=1e-4)
    # Precision over YES calls: 1 correct out of 2 YES calls.
    assert report.precision == pytest.approx(0.5, abs=1e-4)
    # Recall over actual-YES outcomes: 1 actual-YES case, correctly called.
    assert report.recall == pytest.approx(1.0, abs=1e-4)


def test_evaluation_brier_and_log_loss_bounds() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    _seed_snapshot(conn, "m1", "1", 0.9, 0.5, 0.38, "STRONG_YES")
    _seed_resolution(conn, "1", "Yes")
    _seed_snapshot(conn, "m2", "2", 0.1, 0.5, -0.38, "STRONG_NO")
    _seed_resolution(conn, "2", "No")

    report = evaluate_predictions(conn)
    conn.close()

    assert report.brier_score is not None
    assert 0.0 <= report.brier_score <= 1.0
    assert report.log_loss is not None
    assert report.log_loss >= 0.0


def test_evaluation_calibration_buckets_sum_correctly() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    for i in range(5):
        outcome = "Yes" if i % 2 == 0 else "No"
        _seed_snapshot(conn, f"m{i}", str(i), 0.6, 0.5, 0.08, "WATCH_YES")
        _seed_resolution(conn, str(i), outcome)

    report = evaluate_predictions(conn)
    conn.close()

    assert sum(b["n"] for b in report.calibration) == report.n_evaluable


def test_evaluation_no_bet_excluded_from_directional_metrics() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    _seed_snapshot(conn, "m1", "1", 0.51, 0.5, 0.0, "NO_BET")
    _seed_resolution(conn, "1", "Yes")

    report = evaluate_predictions(conn)
    conn.close()

    assert report.n_evaluable == 1
    assert report.n_directional == 0
    assert report.accuracy is None


def test_evaluation_simulated_roi_positive_for_all_correct_calls() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    _seed_snapshot(conn, "m1", "1", 0.9, 0.3, 0.58, "STRONG_YES")
    _seed_resolution(conn, "1", "Yes")

    report = evaluate_predictions(conn)
    conn.close()

    assert report.simulated_roi is not None
    assert report.simulated_roi > 0
