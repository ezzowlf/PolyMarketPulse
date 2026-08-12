"""Regression tests for the category-aware recency-decay fix in
prediction/evidence.py (HANDOFF Part-4 finding: a flat 24h half-life zeroed
the weight of real, still-valid, month-plus-old legislative facts).

Three real, dual-proven cases per the task's own instruction:
(a) a legislative fact from 30+ days ago retains meaningful weight (fixed);
(b) a geopolitical fact from 30+ days ago still correctly decays to
    near-zero (proving the fix was not a blanket loosening);
(c) the existing default behavior for uncategorized evidence types is
    unchanged (no regression for everything else).
"""

from __future__ import annotations

from datetime import UTC, datetime

from polymarketpulse.prediction.evidence import (
    RECENCY_HALF_LIFE_HOURS,
    _recency_weight_local,
)

NOW = datetime(2026, 8, 12, tzinfo=UTC)
THIRTY_DAYS_AGO = "2026-07-13T00:00:00+00:00"


def test_legislative_fact_30_days_old_retains_meaningful_weight():
    """The real, proven bug: a genuine House-passage fact (2025-07-17,
    >1 year old relative to 2026-08-12) must not be treated identically to a
    stale rumor. A 30-day-old legislative fact under the new 720h half-life
    should retain substantial weight (>= 0.5), not decay to ~0."""
    weight = _recency_weight_local(THIRTY_DAYS_AGO, NOW, event_type="legislation")
    assert weight >= 0.49, f"expected legislative fact to retain meaningful weight, got {weight}"

    # And the real, originally-reported case: >1 year old still retains a
    # small but non-zero, honestly-decayed weight (unlike the old bug's 0.0).
    one_year_old = "2025-07-17T15:30:31+00:00"
    old_weight = _recency_weight_local(one_year_old, NOW, event_type="legislation")
    assert old_weight > 0.0, "legislative facts must not hard-zero purely from age"


def test_geopolitical_fact_30_days_old_still_decays_to_near_zero():
    """Proves the fix is a targeted correction, not an accidental global
    loosening: a 30-day-old war_escalation/ceasefire claim must still decay
    to near-zero under the (deliberately stricter-than-default) 12h
    half-life for situational geopolitical facts."""
    weight_war = _recency_weight_local(THIRTY_DAYS_AGO, NOW, event_type="war_escalation")
    weight_ceasefire = _recency_weight_local(THIRTY_DAYS_AGO, NOW, event_type="ceasefire")
    assert weight_war < 0.001
    assert weight_ceasefire < 0.001

    # Also confirm geopolitical decay is at least as strict as the default
    # curve at a shorter horizon (not loosened relative to today's behavior).
    twelve_hours_ago = "2026-08-11T12:00:00+00:00"
    geo_12h = _recency_weight_local(twelve_hours_ago, NOW, event_type="war_escalation")
    default_12h = _recency_weight_local(twelve_hours_ago, NOW, event_type=None)
    assert geo_12h <= default_12h


def test_uncategorized_evidence_default_behavior_unchanged():
    """No regression: evidence with no event_type (or an event_type not in
    the new lookup, e.g. sports/politics office-departure types) must use
    the exact same default RECENCY_HALF_LIFE_HOURS=24.0 curve as before this
    fix, whether or not event_type is explicitly passed."""
    published_at = "2025-07-17T15:30:31+00:00"
    assert _recency_weight_local(published_at, NOW) == 0.0
    assert _recency_weight_local(published_at, NOW, event_type=None) == 0.0
    assert _recency_weight_local(published_at, NOW, event_type="office_departure") == 0.0

    fresh = NOW.isoformat()
    assert _recency_weight_local(fresh, NOW) > 0.9
    assert _recency_weight_local(fresh, NOW, event_type="sport_match") > 0.9

    # Sanity: default curve math itself is untouched.
    hours_ago = 24.0
    expected = round(0.5 ** (hours_ago / RECENCY_HALF_LIFE_HOURS), 4)
    ts = "2026-08-11T00:00:00+00:00"
    assert _recency_weight_local(ts, NOW) == expected


def test_macro_rate_decision_30_days_old_retains_meaningful_weight():
    """A Fed rate decision is 'the current rate' until the next ~6-8 week
    FOMC cycle, not until an arbitrary hour count — same reasoning class as
    legislation, separately proven here since it's a distinct category in
    the lookup."""
    for event_type in ("rate_cut", "rate_hike", "rate_hold"):
        weight = _recency_weight_local(THIRTY_DAYS_AGO, NOW, event_type=event_type)
        assert weight >= 0.49, f"{event_type}: expected meaningful weight, got {weight}"
