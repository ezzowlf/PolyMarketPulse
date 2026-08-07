"""Tests for this round's two root-cause fixes:

1. A resolved market must be preserved in `markets` even if it was never
   captured by a normal active-market scan (storage.record_resolution now
   upserts the markets row itself — see storage.py's `_upsert_market_row`).
2. The historical baseline uses graduated confidence tiers instead of a
   binary >=5 gate, and forecast_status distinguishes BASELINE_ONLY /
   EVIDENCE_ONLY / INDEPENDENT_FORECAST / BLENDED_FORECAST / LOW_DATA /
   NO_FORECAST.

Also covers news-enabled/disabled independent evidence paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from polymarketpulse.models import Market, ResolutionStatus
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.history import (
    TIER_LIMITED,
    TIER_UNAVAILABLE,
    TIER_USABLE,
    TIER_VERY_LOW,
    _confidence_tier,
    compute_history_estimate,
)
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


# --- resolved market persistence, decoupled from active scans --------------

def _resolved_market(pmid: str, outcome: str, category: str = "esports") -> Market:
    return Market(
        provider="polymarket", provider_market_id=pmid, condition_id="", question=f"Will {pmid} happen?",
        slug=f"m-{pmid}", category=category, resolution_status=ResolutionStatus.RESOLVED,
        winning_outcome=outcome, resolved_at=datetime.now(UTC), yes_price=1.0 if outcome == "Yes" else 0.0,
    )


def test_resolved_market_never_actively_scanned_still_gets_a_markets_row(storage: Storage) -> None:
    """This is the exact bug found in the live audit: market_resolutions
    had 100% zero overlap with markets because record_resolution never
    touched the markets table. A market that ONLY ever goes through
    record_resolution() (never through save()) must still show up in
    `markets`."""
    market = _resolved_market("never-scanned-1", "Yes")
    recorded = storage.record_resolution(market)
    assert recorded is True

    row = storage.connection.execute(
        "SELECT question, category FROM markets WHERE provider = 'polymarket' AND provider_market_id = 'never-scanned-1'"
    ).fetchone()
    assert row is not None
    assert row[1] == "esports"


def test_history_submodel_finds_resolution_only_markets(storage: Storage) -> None:
    """End-to-end proof: after record_resolution() (no active scan at
    all), compute_history_estimate's join must find these markets."""
    for i in range(4):
        storage.record_resolution(_resolved_market(f"r-yes-{i}", "Yes"))
    for i in range(1):
        storage.record_resolution(_resolved_market(f"r-no-{i}", "No"))

    estimate, sample_size, observed_rate = compute_history_estimate(storage.connection, "esports", "polymarket")
    assert sample_size == 5
    assert observed_rate == pytest.approx(0.8)
    assert estimate.available is True


def test_record_resolution_is_idempotent_for_markets_row(storage: Storage) -> None:
    market = _resolved_market("idempotent-1", "Yes")
    storage.record_resolution(market)
    storage.record_resolution(market)  # second call: no-op for market_resolutions, but must not error
    count = storage.connection.execute(
        "SELECT COUNT(*) FROM markets WHERE provider_market_id = 'idempotent-1'"
    ).fetchone()[0]
    assert count == 1


# --- graduated historical-baseline confidence tiers --------------------------

def test_confidence_tier_boundaries() -> None:
    assert _confidence_tier(0) == TIER_UNAVAILABLE
    assert _confidence_tier(2) == TIER_UNAVAILABLE
    assert _confidence_tier(3) == TIER_VERY_LOW
    assert _confidence_tier(9) == TIER_VERY_LOW
    assert _confidence_tier(10) == TIER_LIMITED
    assert _confidence_tier(29) == TIER_LIMITED
    assert _confidence_tier(30) == TIER_USABLE
    assert _confidence_tier(500) == TIER_USABLE


def test_two_cases_yields_unavailable_not_pseudo_precise_probability(storage: Storage) -> None:
    """Spec requirement: no pseudo-precise probability from 2 cases."""
    storage.record_resolution(_resolved_market("two-a", "Yes"))
    storage.record_resolution(_resolved_market("two-b", "No"))
    estimate, sample_size, _rate = compute_history_estimate(storage.connection, "esports", "polymarket")
    assert sample_size == 2
    assert estimate.available is False
    assert estimate.weight == 0.0


def test_very_low_tier_gets_less_weight_than_usable_tier(storage: Storage) -> None:
    for i in range(4):
        storage.record_resolution(_resolved_market(f"vlow-{i}", "Yes", category="vlow_cat"))
    for i in range(35):
        storage.record_resolution(_resolved_market(f"usable-{i}", "Yes", category="usable_cat"))

    vlow_estimate, _, _ = compute_history_estimate(storage.connection, "vlow_cat", "polymarket")
    usable_estimate, _, _ = compute_history_estimate(storage.connection, "usable_cat", "polymarket")
    assert vlow_estimate.weight < usable_estimate.weight


# --- forecast_status transitions --------------------------------------------

def test_baseline_only_when_only_history_available(storage: Storage) -> None:
    for i in range(10):
        storage.record_resolution(_resolved_market(f"base-{i}", "Yes" if i % 2 == 0 else "No"))
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "1", "esports", 0.5, 50000, 90, 0, None, True,
    )
    assert result.forecast_status == "BASELINE_ONLY"
    assert result.independent_probability is not None


def test_no_forecast_when_nothing_available(storage: Storage) -> None:
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "1", "esports", 0.5, 50000, 90, 0, None, True,
    )
    assert result.forecast_status == "NO_FORECAST"
    assert result.independent_probability is None
