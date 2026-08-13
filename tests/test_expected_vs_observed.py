"""Phase H — Expected vs Observed: pure derivation tests. Lateness only
ever comes from a real deadline comparison (event_clock.py), never an
invented per-step expected-by date."""

from __future__ import annotations

from polymarketpulse.prediction.event_clock import EventClock
from polymarketpulse.prediction.expected_vs_observed import derive_expected_vs_observed
from polymarketpulse.prediction.next_event import NextEvent
from polymarketpulse.prediction.world_state import ResolutionPath, ResolutionStep


def _clock(**overrides) -> EventClock:
    defaults = {
        "deadline": "2026-12-31", "time_remaining_hours": 500.0, "required_steps_remaining": 2,
        "optional_steps_remaining": None, "estimated_minimum_path_time_hours": None,
        "estimated_typical_path_time_hours": None, "schedule_slack_hours": None,
        "deadline_pressure": "MEDIUM", "path_feasibility": "MEDIUM", "blocking_step": None,
        "timing_confidence": "LOW",
    }
    defaults.update(overrides)
    return EventClock(**defaults)


def test_no_resolution_path_gives_unknown() -> None:
    result = derive_expected_vs_observed(resolution_path=None, next_event=None, event_clock=None)
    assert result.lateness_state == "UNKNOWN"
    assert result.observed is None


def test_already_occurred_marks_last_completed_step_observed() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed", timestamp="2025-01-01T00:00:00+00:00"),
            ResolutionStep(name="committee", status="completed", timestamp="2025-02-01T00:00:00+00:00"),
        ),
    )
    next_event = NextEvent(
        next_event_type=None, next_event_description="Alle Schritte abgeschlossen.",
        expected_time_window=None, status="ALREADY_OCCURRED",
    )
    result = derive_expected_vs_observed(resolution_path=path, next_event=next_event, event_clock=_clock())
    assert result.observed is True
    assert result.observed_at == "2025-02-01T00:00:00+00:00"
    assert result.expected_event == "COMMITTEE"


def test_impossible_feasibility_gives_severely_late_with_real_lateness_hours() -> None:
    path = ResolutionPath(applies=True, template_name="LEGISLATION", steps=())
    next_event = NextEvent(
        next_event_type="SENATE_VOTE", next_event_description="Abstimmung im Senat",
        expected_time_window=None, status="PLAUSIBLE",
    )
    clock = _clock(path_feasibility="IMPOSSIBLE", time_remaining_hours=-12.0)
    result = derive_expected_vs_observed(resolution_path=path, next_event=next_event, event_clock=clock)
    assert result.lateness_state == "SEVERELY_LATE"
    assert result.observed is False
    assert result.lateness_hours == 12.0
    assert "SENATE_VOTE" in result.implication


def test_high_deadline_pressure_without_expiry_gives_at_risk() -> None:
    path = ResolutionPath(applies=True, template_name="LEGISLATION", steps=())
    next_event = NextEvent(
        next_event_type="SENATE_VOTE", next_event_description="Abstimmung im Senat",
        expected_time_window=None, status="PLAUSIBLE",
    )
    clock = _clock(path_feasibility="LOW", deadline_pressure="HIGH", time_remaining_hours=48.0)
    result = derive_expected_vs_observed(resolution_path=path, next_event=next_event, event_clock=clock)
    assert result.lateness_state == "AT_RISK"
    assert result.lateness_hours is None


def test_low_pressure_gives_honest_unknown_not_on_track() -> None:
    """No real duration model exists to positively claim 'on track' -- the
    honest default for the common case (real deadline pressure LOW/MEDIUM,
    no expiry) is UNKNOWN, not a fabricated ON_TRACK."""
    path = ResolutionPath(applies=True, template_name="LEGISLATION", steps=())
    next_event = NextEvent(
        next_event_type="SENATE_VOTE", next_event_description="Abstimmung im Senat",
        expected_time_window=None, status="PLAUSIBLE",
    )
    clock = _clock(path_feasibility="HIGH", deadline_pressure="LOW", time_remaining_hours=3000.0)
    result = derive_expected_vs_observed(resolution_path=path, next_event=next_event, event_clock=clock)
    assert result.lateness_state == "UNKNOWN"
    assert result.observed is False
