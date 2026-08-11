"""Block E Part 3: Change Attribution. Real, constructed tests against a
real sqlite Storage — inserts real prediction_snapshots rows (via the
storage API) and a real events row, then verifies the derived attribution,
including the "no real correlated event -> honestly None" case."""

from __future__ import annotations

from pathlib import Path

import pytest

from polymarketpulse.prediction.change_attribution import compute_change_attributions
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _snapshot(storage: Storage, market_id: str, published: float | None, created_at: str) -> None:
    storage.connection.execute(
        """
        INSERT INTO prediction_snapshots (
            market_id, provider, provider_market_id, category, prediction_version, created_at,
            market_yes_probability, estimated_yes_probability, net_yes_edge, confidence_score,
            recommendation, comparable_sample_size, published_forecast_probability
        ) VALUES (?, 'polymarket', 'pm-1', 'politics', 'v2', ?, 0.4, 0.5, 0.1, 80.0, 'YES', 10, ?)
        """,
        (market_id, created_at, published),
    )
    storage.connection.commit()


def test_no_change_between_identical_snapshots_produces_no_attribution(storage: Storage) -> None:
    _snapshot(storage, "m1", 0.41, "2026-08-01T00:00:00+00:00")
    _snapshot(storage, "m1", 0.41, "2026-08-02T00:00:00+00:00")
    assert compute_change_attributions(storage, "m1") == []


def test_real_change_without_correlated_event_has_no_fabricated_attribution(storage: Storage) -> None:
    _snapshot(storage, "m1", 0.41, "2026-08-01T00:00:00+00:00")
    _snapshot(storage, "m1", 0.49, "2026-08-02T00:00:00+00:00")
    attrs = compute_change_attributions(storage, "m1")
    assert len(attrs) == 1
    a = attrs[0]
    assert a.previous_forecast == 0.41
    assert a.new_forecast == 0.49
    assert round(a.delta, 2) == 0.08
    assert a.triggering_claim is None
    assert a.source is None
    assert a.reason is None


def test_real_change_with_correlated_event_gets_real_attribution(storage: Storage) -> None:
    _snapshot(storage, "m2", 0.41, "2026-08-01T00:00:00+00:00")
    _snapshot(storage, "m2", 0.49, "2026-08-03T00:00:00+00:00")
    storage.connection.execute(
        """
        INSERT INTO events (title, event_type, occurred_at, source, created_at, provider, provider_market_id)
        VALUES ('Senatsabstimmung offiziell angesetzt', 'legislative_vote', '2026-08-02T12:00:00+00:00',
                'senate.gov', '2026-08-02T12:05:00+00:00', 'polymarket', 'pm-1')
        """
    )
    storage.connection.commit()

    attrs = compute_change_attributions(storage, "m2")
    assert len(attrs) == 1
    a = attrs[0]
    assert a.triggering_claim == "Senatsabstimmung offiziell angesetzt"
    assert a.source == "senate.gov"
    assert "Senatsabstimmung offiziell angesetzt" in a.reason
    assert "senate.gov" in a.reason


def test_event_outside_window_is_not_attributed(storage: Storage) -> None:
    _snapshot(storage, "m3", 0.41, "2026-08-01T00:00:00+00:00")
    _snapshot(storage, "m3", 0.49, "2026-08-02T00:00:00+00:00")
    # event occurred AFTER the newer snapshot -- must not be picked up
    storage.connection.execute(
        """
        INSERT INTO events (title, event_type, occurred_at, source, created_at, provider, provider_market_id)
        VALUES ('Late unrelated event', 'legislative_vote', '2026-08-05T00:00:00+00:00',
                'example.com', '2026-08-05T00:00:00+00:00', 'polymarket', 'pm-1')
        """
    )
    storage.connection.commit()
    attrs = compute_change_attributions(storage, "m3")
    assert len(attrs) == 1
    assert attrs[0].triggering_claim is None


def test_fewer_than_two_snapshots_returns_empty(storage: Storage) -> None:
    _snapshot(storage, "m4", 0.5, "2026-08-01T00:00:00+00:00")
    assert compute_change_attributions(storage, "m4") == []
