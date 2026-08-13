"""Phase G — Event Clock: pure derivation tests. No fabricated durations
-- estimated_minimum/typical_path_time stay None (no real per-step
duration dataset exists), except the deterministic IMPOSSIBLE state which
needs only a plain clock comparison."""

from __future__ import annotations

from polymarketpulse.prediction.event_clock import derive_event_clock
from polymarketpulse.prediction.next_event import NextEvent
from polymarketpulse.prediction.world_state import ResolutionPath, ResolutionStep


def _path(**overrides) -> ResolutionPath:
    defaults = {
        "applies": True, "template_name": "LEGISLATION",
        "steps": (
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
            ResolutionStep(name="house_vote", status="completed"),
            ResolutionStep(name="senate_vote", status="unknown"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
        "steps_remaining": 2,
        "deadline_pressure": "MEDIUM",
        "path_feasibility": "MEDIUM",
    }
    defaults.update(overrides)
    return ResolutionPath(**defaults)


def test_no_resolution_path_gives_unknown_feasibility() -> None:
    clock = derive_event_clock(
        deadline="2026-12-31", time_remaining_hours=1000.0, resolution_path=None, next_event=None,
    )
    assert clock.path_feasibility == "UNKNOWN"
    assert clock.timing_confidence == "UNKNOWN"
    assert clock.estimated_minimum_path_time_hours is None
    assert clock.estimated_typical_path_time_hours is None


def test_open_work_with_expired_deadline_is_impossible() -> None:
    path = _path()
    clock = derive_event_clock(
        deadline="2026-01-01", time_remaining_hours=-5.0, resolution_path=path, next_event=None,
    )
    assert clock.path_feasibility == "IMPOSSIBLE"
    assert clock.timing_confidence == "HIGH"


def test_open_work_before_deadline_reuses_resolution_path_feasibility() -> None:
    path = _path(path_feasibility="MEDIUM")
    clock = derive_event_clock(
        deadline="2026-12-31", time_remaining_hours=500.0, resolution_path=path, next_event=None,
    )
    assert clock.path_feasibility == "MEDIUM"
    assert clock.timing_confidence == "LOW"  # no real duration dataset -> honestly low confidence
    assert clock.required_steps_remaining == 2


def test_no_open_work_left_never_impossible_even_past_deadline() -> None:
    """A fully completed path with an expired deadline is not
    'IMPOSSIBLE' -- there is no remaining work the clock could have run
    out on."""
    path = _path(steps_remaining=0, path_feasibility="HIGH")
    clock = derive_event_clock(
        deadline="2026-01-01", time_remaining_hours=-5.0, resolution_path=path, next_event=None,
    )
    assert clock.path_feasibility != "IMPOSSIBLE"


def test_blocking_step_populated_from_blocked_next_event() -> None:
    path = _path()
    next_event = NextEvent(
        next_event_type="SENATE_VOTE", next_event_description="Abstimmung im Senat",
        expected_time_window=None, status="BLOCKED",
    )
    clock = derive_event_clock(
        deadline="2026-12-31", time_remaining_hours=500.0, resolution_path=path, next_event=next_event,
    )
    assert clock.blocking_step == "senate_vote"


def test_no_blocking_step_when_next_event_not_blocked() -> None:
    path = _path()
    next_event = NextEvent(
        next_event_type="SENATE_VOTE", next_event_description="Abstimmung im Senat",
        expected_time_window=None, status="PLAUSIBLE",
    )
    clock = derive_event_clock(
        deadline="2026-12-31", time_remaining_hours=500.0, resolution_path=path, next_event=next_event,
    )
    assert clock.blocking_step is None


def test_deadline_pressure_passed_through_from_resolution_path() -> None:
    path = _path(deadline_pressure="CRITICAL")
    clock = derive_event_clock(
        deadline="2026-12-31", time_remaining_hours=10.0, resolution_path=path, next_event=None,
    )
    assert clock.deadline_pressure == "CRITICAL"
