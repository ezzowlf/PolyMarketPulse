import sqlite3
from datetime import UTC, datetime

import pytest

from polymarketpulse.events import TIER_KNOWN, TIER_PLAUSIBLE, TIER_SPECULATIVE
from polymarketpulse.migrations import run_migrations
from polymarketpulse.prediction.event_relations import (
    collect_event_relation_signals,
    compute_event_relation_estimate,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    run_migrations(c)
    yield c
    c.close()


def _insert_relation(conn, evidence_tier, direction="positive", strength=0.5, confidence=0.8, relation_type="CONTRIBUTES_TO"):
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO event_relations (relation_type, direction, evidence_tier, strength, confidence, "
        "target_provider, target_provider_market_id, evidence_count, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'polymarket', 'm1', 1, 'test relation', ?)",
        (relation_type, direction, evidence_tier, strength, confidence, now),
    )
    conn.commit()


def test_no_relations_is_unavailable(conn) -> None:
    signals = collect_event_relation_signals(conn, "polymarket", "m1")
    estimate = compute_event_relation_estimate(signals, 0.5)
    assert estimate.available is False
    assert estimate.weight == 0.0


def test_speculative_relation_contributes_nothing_quantitatively(conn) -> None:
    _insert_relation(conn, TIER_SPECULATIVE)
    signals = collect_event_relation_signals(conn, "polymarket", "m1")
    estimate = compute_event_relation_estimate(signals, 0.5)
    assert estimate.available is False
    assert estimate.weight == 0.0
    assert estimate.estimated_yes_probability is None


def test_plausible_relation_also_contributes_nothing(conn) -> None:
    _insert_relation(conn, TIER_PLAUSIBLE)
    signals = collect_event_relation_signals(conn, "polymarket", "m1")
    estimate = compute_event_relation_estimate(signals, 0.5)
    assert estimate.available is False


def test_known_tier_relation_produces_bounded_adjustment(conn) -> None:
    _insert_relation(conn, TIER_KNOWN, direction="positive", strength=1.0, confidence=1.0)
    signals = collect_event_relation_signals(conn, "polymarket", "m1")
    estimate = compute_event_relation_estimate(signals, 0.5)
    assert estimate.available is True
    assert estimate.estimated_yes_probability is not None
    assert estimate.estimated_yes_probability > 0.5
    # capped at MAX_EVENT_RELATION_ADJUSTMENT even though strength*confidence=1.0
    assert estimate.estimated_yes_probability <= 0.55 + 1e-9


def test_negative_direction_lowers_estimate(conn) -> None:
    _insert_relation(conn, TIER_KNOWN, direction="negative", strength=1.0, confidence=1.0)
    signals = collect_event_relation_signals(conn, "polymarket", "m1")
    estimate = compute_event_relation_estimate(signals, 0.5)
    assert estimate.estimated_yes_probability < 0.5


def test_no_market_price_means_unavailable_even_with_known_relation(conn) -> None:
    _insert_relation(conn, TIER_KNOWN, strength=1.0, confidence=1.0)
    signals = collect_event_relation_signals(conn, "polymarket", "m1")
    estimate = compute_event_relation_estimate(signals, None)
    assert estimate.available is False


def test_mixed_quantitative_and_speculative_only_uses_quantitative(conn) -> None:
    _insert_relation(conn, TIER_SPECULATIVE, direction="positive", strength=0.9, confidence=0.9)
    _insert_relation(conn, TIER_KNOWN, direction="positive", strength=0.1, confidence=0.1)
    signals = collect_event_relation_signals(conn, "polymarket", "m1")
    estimate = compute_event_relation_estimate(signals, 0.5)
    assert estimate.available is True
    # Only the KNOWN relation (small strength*confidence) should move the
    # needle — nowhere near what the speculative one alone would imply.
    assert estimate.estimated_yes_probability <= 0.51 + 1e-9
