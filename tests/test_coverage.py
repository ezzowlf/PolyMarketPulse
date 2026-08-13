"""Real tests for coverage.compute_coverage — real, DB-derived Live Evidence
Engine coverage numbers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from polymarketpulse.coverage import compute_coverage
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _insert_market(storage: Storage, pmid: str, resolved: bool = False) -> None:
    now = datetime.now(UTC).isoformat()
    storage.connection.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
        "first_seen_at, last_seen_at, resolution_status) "
        "VALUES (?, 'polymarket', ?, 'Q?', ?, 'https://x', ?, ?, ?)",
        (pmid, pmid, pmid, now, now, "resolved" if resolved else "open"),
    )
    storage.connection.commit()


def test_empty_db_reports_all_zero(storage: Storage) -> None:
    report = compute_coverage(storage)
    assert report.markets_total == 0
    assert report.markets_unresolved == 0
    assert report.no_forecast_count == 0


def test_resolved_markets_excluded_from_unresolved_counts(storage: Storage) -> None:
    _insert_market(storage, "resolved-1", resolved=True)
    _insert_market(storage, "open-1", resolved=False)
    report = compute_coverage(storage)
    assert report.markets_total == 2
    assert report.markets_unresolved == 1


def test_no_snapshot_counts_as_no_forecast_and_insufficient_evidence(storage: Storage) -> None:
    _insert_market(storage, "open-1", resolved=False)
    report = compute_coverage(storage)
    assert report.no_forecast_count == 1
    assert report.top_blockers.get("insufficient_evidence") == 1


def test_published_forecast_snapshot_counts_correctly(storage: Storage) -> None:
    _insert_market(storage, "open-1", resolved=False)
    now = datetime.now(UTC).isoformat()
    storage.connection.execute(
        "INSERT INTO prediction_snapshots (market_id, provider, provider_market_id, category, "
        "prediction_version, created_at, recommendation, comparable_sample_size, "
        "model_hypothesis_probability, evidence_backed_probability, published_forecast_probability, "
        "evidence_count, independent_confirmation_count) "
        "VALUES ('open-1', 'polymarket', 'open-1', 'x', '1', ?, 'WATCH', 0, 0.6, 0.6, 0.6, 3, 2)",
        (now,),
    )
    storage.connection.commit()
    report = compute_coverage(storage)
    assert report.markets_with_model_hypothesis == 1
    assert report.markets_with_evidence_backed_forecast == 1
    assert report.markets_with_published_forecast == 1
    assert report.markets_with_multiple_independent_groups == 1
    assert report.no_forecast_count == 0
