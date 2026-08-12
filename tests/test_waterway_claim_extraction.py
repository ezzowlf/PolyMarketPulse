"""Part 2 (Live Evidence Engine continuation): waterway headlines can now
become persisted claims. Before this round, `semantics.extract_event` had
no action family for waterway operational-status language at all
(`_ACTION_FAMILIES` only covered office-departure/conflict phrasing — see
HANDOFF.md's Round 3 finding), so `claims.extract_claim_from_event`
(which requires `event.action` to be set) always returned None for real
waterway headlines like the UN News Hormuz article used in this round's
live re-run. These tests cover the new `waterway_status` action family
added to unlock that, without touching classify_evidence_relation's
direction-aware waterway branch added in Part 1 (see
test_waterway_direction_disambiguation.py)."""

from polymarketpulse.prediction.semantics import extract_event  # noqa: I001
from polymarketpulse.claims import extract_claim_from_event

# Importing polymarketpulse.claims before polymarketpulse.prediction is
# fully initialized hits a real circular import (claims.py -> prediction/
# __init__.py -> engine.py -> evidence.py -> claims.py again). Importing
# semantics first (above) resolves the prediction package first when this
# module is collected standalone, sidestepping that; kept in this order
# deliberately rather than ruff's default alphabetical isort.

REAL_UN_NEWS_TITLE = "Strait of Hormuz disruption hits energy, fertilizer and industrial trade"


def test_extract_event_recognizes_waterway_disruption_headline():
    event = extract_event(REAL_UN_NEWS_TITLE)
    assert event.action == "waterway_status"
    assert event.event_type == "strategic_waterway"
    assert event.target == "DEGRADED"
    assert event.status == "actual"


def test_extract_claim_from_event_persists_waterway_claim():
    event = extract_event(REAL_UN_NEWS_TITLE)
    claim = extract_claim_from_event(event, source_id="un_news", source_url="https://news.un.org/x")
    assert claim is not None
    assert claim.event_type == "strategic_waterway"
    assert claim.direction == "negative"  # DEGRADED != NORMAL
    assert "degraded" in claim.predicate or "disrupted" in claim.predicate


def test_extract_claim_from_event_normal_state_is_positive_direction():
    event = extract_event("Strait of Hormuz traffic officially returns to normal, insurers say")
    assert event.target == "NORMAL"
    claim = extract_claim_from_event(event, source_id="reuters")
    assert claim is not None
    assert claim.direction == "positive"


def test_extract_claim_from_event_closed_state_is_negative_direction():
    event = extract_event("Strait of Hormuz effectively closed after military strike")
    assert event.target == "CLOSED"
    claim = extract_claim_from_event(event, source_id="apnews")
    assert claim is not None
    assert claim.direction == "negative"
