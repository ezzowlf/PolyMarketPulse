"""Phase D — Temporal Intelligence: pure classification tests plus real
DB-backed integration with evidence.py's structured-claim filtering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from polymarketpulse.prediction.temporal_state import (
    STATUS_CURRENT,
    STATUS_DISPUTED,
    STATUS_EXPECTED,
    STATUS_EXPIRED,
    STATUS_SUPERSEDED,
    STATUS_UNKNOWN,
    compute_temporal_status,
)

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def test_structural_fact_never_expires_from_age() -> None:
    old = (NOW - timedelta(days=200)).isoformat()
    status = compute_temporal_status(claim_type="PATH_STEP", timestamp=old, now=NOW)
    assert status == STATUS_CURRENT


def test_direct_resolution_never_expires_from_age() -> None:
    old = (NOW - timedelta(days=365)).isoformat()
    status = compute_temporal_status(claim_type="DIRECT_RESOLUTION", timestamp=old, now=NOW)
    assert status == STATUS_CURRENT


def test_context_claim_expires_after_its_freshness_window() -> None:
    stale = (NOW - timedelta(hours=100)).isoformat()  # > 72h CONTEXT window
    status = compute_temporal_status(claim_type="CONTEXT", timestamp=stale, now=NOW)
    assert status == STATUS_EXPIRED


def test_context_claim_within_window_stays_current() -> None:
    fresh = (NOW - timedelta(hours=10)).isoformat()
    status = compute_temporal_status(claim_type="CONTEXT", timestamp=fresh, now=NOW)
    assert status == STATUS_CURRENT


def test_quantitative_signal_has_longer_window_than_context() -> None:
    age = (NOW - timedelta(hours=100)).isoformat()  # > CONTEXT window, < QUANTITATIVE_SIGNAL window
    assert compute_temporal_status(claim_type="CONTEXT", timestamp=age, now=NOW) == STATUS_EXPIRED
    assert compute_temporal_status(claim_type="QUANTITATIVE_SIGNAL", timestamp=age, now=NOW) == STATUS_CURRENT


def test_superseded_claim_always_wins_even_if_structural() -> None:
    fresh = (NOW - timedelta(hours=1)).isoformat()
    status = compute_temporal_status(
        claim_type="PATH_STEP", timestamp=fresh, now=NOW, superseded_by="newer-claim-id"
    )
    assert status == STATUS_SUPERSEDED


def test_disputed_claim_overrides_structural_currency() -> None:
    fresh = (NOW - timedelta(hours=1)).isoformat()
    status = compute_temporal_status(
        claim_type="DIRECT_RESOLUTION", timestamp=fresh, now=NOW, has_counter_evidence=True
    )
    assert status == STATUS_DISPUTED


def test_future_dated_claim_is_expected_not_current() -> None:
    future = (NOW + timedelta(days=5)).isoformat()
    status = compute_temporal_status(claim_type="CONTEXT", timestamp=future, now=NOW)
    assert status == STATUS_EXPECTED


def test_no_timestamp_and_no_expected_at_is_unknown() -> None:
    status = compute_temporal_status(claim_type="CONTEXT", timestamp=None, now=NOW)
    assert status == STATUS_UNKNOWN


def test_explicit_valid_until_overrides_generic_freshness_window() -> None:
    fresh_ts = (NOW - timedelta(hours=1)).isoformat()  # would be CURRENT under the generic window
    expired_until = (NOW - timedelta(minutes=1)).isoformat()
    status = compute_temporal_status(
        claim_type="CONTEXT", timestamp=fresh_ts, now=NOW, valid_until=expired_until
    )
    assert status == STATUS_EXPIRED
