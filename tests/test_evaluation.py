import sqlite3
from datetime import UTC, datetime

import pytest

from polymarketpulse.evaluation import (
    evaluate_forecast_history,
    evaluate_model_hypothesis_history,
    evaluate_predictions,
    evaluate_source_performance,
)
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


# --- BLOCK G Part 1/2: published_forecast_probability evaluation --------


def _seed_block_e_snapshot(
    conn, provider_market_id, published_prob, category, models_used, forecast_at=None
):
    conn.execute(
        "INSERT INTO prediction_snapshots (market_id, provider, provider_market_id, category, "
        "prediction_version, created_at, recommendation, comparable_sample_size, forecast_at, "
        "published_forecast_probability, models_used) "
        "VALUES (?, 'polymarket', ?, ?, 'v2', ?, 'WATCH', 10, ?, ?, ?)",
        (
            provider_market_id, provider_market_id, category,
            datetime.now(UTC).isoformat(), forecast_at or datetime(2020, 1, 1, tzinfo=UTC).isoformat(),
            published_prob, models_used,
        ),
    )
    conn.commit()


def test_evaluate_forecast_history_on_empty_db(conn: sqlite3.Connection) -> None:
    report = evaluate_forecast_history(conn)
    assert report.status == "UNCALIBRATED"
    assert report.matched_pair_count == 0
    assert report.by_category == []
    assert report.by_submodel == []


def test_evaluate_forecast_history_never_scores_null_published_probability(conn: sqlite3.Connection) -> None:
    # A snapshot with published_forecast_probability=NULL (correct
    # NO_POSITION) must NEVER be coerced into a scored pair, even though
    # it resolves and even though older fields (market_yes_probability)
    # might be present.
    _seed_snapshot(conn, "m1", "1", None, 0.3, None, "NO_BET")
    _seed_resolution(conn, "1", "Yes")

    report = evaluate_forecast_history(conn)
    assert report.matched_pair_count == 0
    assert report.status == "UNCALIBRATED"


def test_evaluate_forecast_history_scores_published_forecasts_below_threshold_with_real_n(
    conn: sqlite3.Connection,
) -> None:
    _seed_block_e_snapshot(conn, "1", 0.9, "politics", "history,momentum")
    _seed_resolution(conn, "1", "Yes")

    report = evaluate_forecast_history(conn)
    assert report.matched_pair_count == 1
    assert report.status == "UNCALIBRATED"  # below MIN_MATCHED_PAIRS_FOR_CALIBRATION
    assert len(report.by_category) == 1
    assert report.by_category[0].key == "politics"
    assert report.by_category[0].n == 1
    assert report.by_category[0].too_small is True
    submodel_keys = {s.key for s in report.by_submodel}
    assert submodel_keys == {"history", "momentum"}


def test_evaluate_forecast_history_excludes_lookahead_forecasts(conn: sqlite3.Connection) -> None:
    # forecast_at AFTER resolved_at must be excluded entirely.
    _seed_block_e_snapshot(conn, "1", 0.9, "politics", "history", forecast_at=datetime(2099, 1, 1, tzinfo=UTC).isoformat())
    _seed_resolution(conn, "1", "Yes")

    report = evaluate_forecast_history(conn)
    assert report.matched_pair_count == 0


# --- PART 11: model_hypothesis_probability evaluation (distinct from ------
# published_forecast_probability above; scores the raw model estimate,
# which is populated far more often than the gated published field) -------


def _seed_model_hypothesis_snapshot(
    conn, provider_market_id, model_hypothesis_prob, category, models_used, forecast_at=None
):
    conn.execute(
        "INSERT INTO prediction_snapshots (market_id, provider, provider_market_id, category, "
        "prediction_version, created_at, recommendation, comparable_sample_size, forecast_at, "
        "model_hypothesis_probability, models_used) "
        "VALUES (?, 'polymarket', ?, ?, 'v2', ?, 'WATCH', 10, ?, ?, ?)",
        (
            provider_market_id, provider_market_id, category,
            datetime.now(UTC).isoformat(), forecast_at or datetime(2020, 1, 1, tzinfo=UTC).isoformat(),
            model_hypothesis_prob, models_used,
        ),
    )
    conn.commit()


def test_evaluate_model_hypothesis_history_on_empty_db(conn: sqlite3.Connection) -> None:
    report = evaluate_model_hypothesis_history(conn)
    assert report.status == "UNCALIBRATED"
    assert report.matched_pair_count == 0


def test_evaluate_model_hypothesis_history_never_scores_null_model_hypothesis(conn: sqlite3.Connection) -> None:
    # A published forecast with no model_hypothesis_probability must never
    # be coerced into a scored pair for this path.
    _seed_block_e_snapshot(conn, "1", 0.9, "politics", "history")
    _seed_resolution(conn, "1", "Yes")

    report = evaluate_model_hypothesis_history(conn)
    assert report.matched_pair_count == 0


def test_evaluate_model_hypothesis_history_scores_suppressed_forecasts(conn: sqlite3.Connection) -> None:
    # Real-world case this exists for: a market where the evidence gate
    # withheld publication (published_forecast_probability stays NULL) but
    # the specialized model still produced a raw hypothesis. This must be
    # scoreable here even though evaluate_forecast_history would report 0.
    _seed_model_hypothesis_snapshot(conn, "1", 0.85, "geopolitics", "history,momentum")
    _seed_resolution(conn, "1", "No")

    published_report = evaluate_forecast_history(conn)
    hypothesis_report = evaluate_model_hypothesis_history(conn)

    assert published_report.matched_pair_count == 0
    assert hypothesis_report.matched_pair_count == 1
    assert hypothesis_report.by_category[0].key == "geopolitics"
    submodel_keys = {s.key for s in hypothesis_report.by_submodel}
    assert submodel_keys == {"history", "momentum"}


def test_evaluate_model_hypothesis_history_excludes_lookahead_forecasts(conn: sqlite3.Connection) -> None:
    _seed_model_hypothesis_snapshot(
        conn, "1", 0.7, "politics", "history", forecast_at=datetime(2099, 1, 1, tzinfo=UTC).isoformat()
    )
    _seed_resolution(conn, "1", "Yes")

    report = evaluate_model_hypothesis_history(conn)
    assert report.matched_pair_count == 0


# --- BLOCK G Part 3: source performance -----------------------------------


def test_evaluate_source_performance_uses_real_claim_market_links_when_present(conn: sqlite3.Connection) -> None:
    """Real gap closed this round: migration 25 adds claim_market_links
    (research_runner.py now populates it for real GovTrack/PortWatch
    claims), so evaluate_source_performance's already-built real
    computation path is now genuinely reachable, not just theoretical."""
    report = evaluate_source_performance(conn)
    assert report.linkage_available is True
    assert report.by_source == []  # no claims/resolutions seeded in this fixture yet, honestly empty
