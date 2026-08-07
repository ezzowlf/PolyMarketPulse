import sqlite3

import pytest

from polymarketpulse.events import (
    QUANTITATIVE_TIERS,
    TIER_KNOWN,
    TIER_PLAUSIBLE,
    TIER_SPECULATIVE,
    compute_event_market_relevance,
    quantitative_weight_for_tier,
    resolve_entity,
    seed_default_entities,
    validate_relation_tier,
)
from polymarketpulse.migrations import run_migrations


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    run_migrations(c)
    seed_default_entities(c)
    yield c
    c.close()


def test_aliases_resolve_to_canonical_entity(conn) -> None:
    assert resolve_entity(conn, "Deutschland") == "germany"
    assert resolve_entity(conn, "DFB team") == "germany"
    assert resolve_entity(conn, "München") == "munich"
    assert resolve_entity(conn, "Straße von Hormus") == "strait of hormuz"


def test_unknown_entity_resolves_to_none_not_a_guess(conn) -> None:
    assert resolve_entity(conn, "Some Completely Unrelated Thing XYZ") is None


def test_seeding_is_idempotent(conn) -> None:
    seed_default_entities(conn)  # second call must not raise or duplicate
    count = conn.execute("SELECT COUNT(*) FROM entities WHERE canonical_name = 'germany'").fetchone()[0]
    assert count == 1


def test_causes_requires_strong_evidence() -> None:
    with pytest.raises(ValueError):
        validate_relation_tier("CAUSES", TIER_PLAUSIBLE)
    validate_relation_tier("CAUSES", TIER_KNOWN)  # must not raise


def test_only_quantitative_tiers_get_nonzero_weight() -> None:
    for tier in QUANTITATIVE_TIERS:
        assert quantitative_weight_for_tier(tier) == 1.0
    assert quantitative_weight_for_tier(TIER_PLAUSIBLE) == 0.0
    assert quantitative_weight_for_tier(TIER_SPECULATIVE) == 0.0
    assert quantitative_weight_for_tier("UNKNOWN") == 0.0


def test_relevance_requires_shared_terms() -> None:
    result = compute_event_market_relevance(
        "Germany reaches World Cup semifinal", "Will Bitcoin exceed $100,000 by December?"
    )
    assert result.relevance_score == 0.0


def test_relevance_scores_shared_terms_higher() -> None:
    result = compute_event_market_relevance(
        "Germany reaches World Cup semifinal against Argentina",
        "Will Germany win the World Cup semifinal?",
        event_geo="country", market_geo="country",
    )
    assert result.relevance_score > 0.0
    assert result.entity_overlap > 0.0


def test_geographic_decay_reduces_cross_scope_relevance() -> None:
    same_scope = compute_event_market_relevance(
        "Munich hosts major event downtown", "Will Munich event attendance exceed forecast?",
        event_geo="city", market_geo="city",
    )
    cross_scope = compute_event_market_relevance(
        "Munich hosts major event downtown", "Will Munich event attendance exceed forecast?",
        event_geo="city", market_geo="global",
    )
    assert cross_scope.relevance_score < same_scope.relevance_score
