"""Phase N2: tests for the calibration framework (calibration.py) —
toy-example correctness for brier_score/log_loss/calibration_bins/
error_by_model_family, plus compute_calibration_report's real DB join,
its look-ahead guard, and its UNCALIBRATED-below-threshold behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.prediction.calibration import (
    MIN_MATCHED_PAIRS_FOR_CALIBRATION,
    brier_score,
    calibration_bins,
    compute_calibration_report,
    error_by_model_family,
    log_loss,
)
from polymarketpulse.storage import Storage

# --- toy datasets ------------------------------------------------------

# Perfectly calibrated: within each probability level, the empirical
# frequency of True exactly matches the predicted probability.
#   p=0.9 -> 9/10 True, p=0.1 -> 1/10 True
_PERFECTLY_CALIBRATED = (
    [(0.9, True)] * 9 + [(0.9, False)] * 1
    + [(0.1, True)] * 1 + [(0.1, False)] * 9
)

# Systematically overconfident: always predicts 0.9 but is only right 60%
# of the time.
_OVERCONFIDENT = [(0.9, True)] * 6 + [(0.9, False)] * 4


def test_brier_score_perfect_prediction_is_zero() -> None:
    assert brier_score([(1.0, True), (0.0, False)]) == pytest.approx(0.0)


def test_brier_score_maximally_wrong_is_one() -> None:
    assert brier_score([(1.0, False), (0.0, True)]) == pytest.approx(1.0)


def test_brier_score_overconfident_toy_dataset_has_specific_signed_bias() -> None:
    # Hand-computable: 6 items (0.9-1)^2 = 0.01, 4 items (0.9-0)^2 = 0.81
    # mean = (6*0.01 + 4*0.81) / 10 = (0.06 + 3.24) / 10 = 0.33
    score = brier_score(_OVERCONFIDENT)
    assert score == pytest.approx(0.33)
    # Materially worse than a well-calibrated 0.6-predicting model would
    # score against the same 60%-True empirical rate:
    #   6*(0.6-1)^2 + 4*(0.6-0)^2 = 6*0.16 + 4*0.36 = 0.96+1.44=2.4 -> /10=0.24
    well_calibrated_equivalent = brier_score([(0.6, True)] * 6 + [(0.6, False)] * 4)
    assert well_calibrated_equivalent == pytest.approx(0.24)
    assert score > well_calibrated_equivalent


def test_brier_score_perfectly_calibrated_toy_dataset_near_zero_relative_to_naive() -> None:
    # Not literally 0 (there's per-item variance even when calibrated in
    # aggregate), but must be much lower than the overconfident dataset's.
    calibrated_score = brier_score(_PERFECTLY_CALIBRATED)
    overconfident_score = brier_score(_OVERCONFIDENT)
    assert calibrated_score < overconfident_score


def test_brier_score_requires_at_least_one_pair() -> None:
    with pytest.raises(ValueError):
        brier_score([])


def test_log_loss_perfect_prediction_near_zero() -> None:
    # Not exactly 0 due to clipping, but must be tiny.
    assert log_loss([(1.0, True), (0.0, False)]) < 1e-6


def test_log_loss_confident_wrong_prediction_is_large() -> None:
    loss = log_loss([(0.99, False)])
    assert loss > 4.0  # -log(0.01) ~= 4.6


def test_log_loss_overconfident_toy_dataset_worse_than_well_calibrated() -> None:
    overconfident_loss = log_loss(_OVERCONFIDENT)
    well_calibrated_loss = log_loss([(0.6, True)] * 6 + [(0.6, False)] * 4)
    assert overconfident_loss > well_calibrated_loss


def test_calibration_bins_perfectly_calibrated_bins_match_prediction() -> None:
    bins = calibration_bins(_PERFECTLY_CALIBRATED, n_bins=10)
    # bucket index for 0.9 with 10 equal-width bins over [0,1] is bin 9 ([0.9,1.0))
    bin_09 = next(b for b in bins if b.bin_lower <= 0.9 < b.bin_upper)
    assert bin_09.count == 10
    assert bin_09.mean_predicted_probability == pytest.approx(0.9)
    assert bin_09.observed_frequency == pytest.approx(0.9)
    # within-bin calibration error near zero
    assert abs(bin_09.mean_predicted_probability - bin_09.observed_frequency) < 1e-9


def test_calibration_bins_overconfident_shows_gap() -> None:
    bins = calibration_bins(_OVERCONFIDENT, n_bins=10)
    bin_09 = next(b for b in bins if b.bin_lower <= 0.9 < b.bin_upper)
    assert bin_09.count == 10
    assert bin_09.mean_predicted_probability == pytest.approx(0.9)
    assert bin_09.observed_frequency == pytest.approx(0.6)
    gap = bin_09.mean_predicted_probability - bin_09.observed_frequency
    assert gap == pytest.approx(0.3)  # overconfident by 30 points


def test_calibration_bins_empty_bins_reported_with_zero_count() -> None:
    bins = calibration_bins([(0.05, True)], n_bins=10)
    non_empty = [b for b in bins if b.count > 0]
    assert len(non_empty) == 1
    assert len(bins) == 10
    empty = [b for b in bins if b.count == 0]
    assert all(b.mean_predicted_probability is None and b.observed_frequency is None for b in empty)


def test_error_by_model_family_groups_correctly() -> None:
    triples = [
        ("history", 0.9, True), ("history", 0.9, False),  # brier = (0.01+0.81)/2 = 0.41
        ("evidence", 1.0, True), ("evidence", 0.0, False),  # brier = 0
    ]
    result = error_by_model_family(triples)
    assert result["history"] == pytest.approx(0.41)
    assert result["evidence"] == pytest.approx(0.0)


# --- compute_calibration_report: real DB join, look-ahead guard, threshold --

@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _insert_snapshot(storage: Storage, pmid: str, forecast_at: str, calibrated_probability: float) -> None:
    storage.connection.execute(
        """
        INSERT INTO prediction_snapshots (
            market_id, provider, provider_market_id, category, prediction_version, created_at,
            recommendation, comparable_sample_size, forecast_at, calibrated_probability, models_used
        ) VALUES (?, 'polymarket', ?, 'geopolitics', 'v2', ?, 'hold', 0, ?, ?, 'history')
        """,
        (pmid, pmid, forecast_at, forecast_at, calibrated_probability),
    )
    storage.connection.commit()


def _insert_resolution(storage: Storage, pmid: str, resolved_at: str, winning_outcome: str) -> None:
    storage.connection.execute(
        """
        INSERT INTO market_resolutions (
            provider, provider_market_id, resolved_at, winning_outcome, status, detected_at
        ) VALUES ('polymarket', ?, ?, ?, 'resolved', ?)
        """,
        (pmid, resolved_at, winning_outcome, resolved_at),
    )
    storage.connection.commit()


def test_compute_calibration_report_below_threshold_is_uncalibrated_with_honest_count(storage: Storage) -> None:
    now = datetime.now(UTC)
    for i in range(5):
        pmid = f"m-{i}"
        _insert_snapshot(storage, pmid, (now - timedelta(days=2)).isoformat(), 0.7)
        _insert_resolution(storage, pmid, (now - timedelta(days=1)).isoformat(), "Yes")

    report = compute_calibration_report(storage.connection)
    assert report.status == "UNCALIBRATED"
    assert report.matched_pair_count == 5
    assert report.min_required == MIN_MATCHED_PAIRS_FOR_CALIBRATION
    assert report.brier_score is None
    assert report.log_loss_value is None
    assert report.bins == ()


def test_compute_calibration_report_excludes_pairs_with_forecast_after_resolution(storage: Storage) -> None:
    """Hard look-ahead guard: a snapshot whose forecast_at is AFTER the
    resolution's resolved_at must be excluded from the matched set."""
    now = datetime.now(UTC)
    # 25 legitimately pre-resolution pairs (above threshold).
    for i in range(25):
        pmid = f"legit-{i}"
        _insert_snapshot(storage, pmid, (now - timedelta(days=2)).isoformat(), 0.6)
        _insert_resolution(storage, pmid, (now - timedelta(days=1)).isoformat(), "Yes")

    # One look-ahead-violating pair: forecast_at is AFTER resolved_at.
    _insert_snapshot(storage, "leaky", (now + timedelta(days=5)).isoformat(), 0.99)
    _insert_resolution(storage, "leaky", now.isoformat(), "No")

    report = compute_calibration_report(storage.connection)
    assert report.status == "CALIBRATED"
    assert report.matched_pair_count == 25  # the leaky pair is excluded
    assert report.brier_score is not None


def test_compute_calibration_report_real_local_db_honest_when_no_matches() -> None:
    """Smoke-level guard for the exact honesty requirement: a completely
    fresh DB (post-migration, zero forecasts/resolutions) must report
    UNCALIBRATED with matched_pair_count == 0, never a fabricated score."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        s = Storage(Path(d) / "fresh.db")
        try:
            report = compute_calibration_report(s.connection)
            assert report.status == "UNCALIBRATED"
            assert report.matched_pair_count == 0
            assert report.brier_score is None
        finally:
            s.close()
