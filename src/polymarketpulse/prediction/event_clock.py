"""Phase G — Event Clock.

Answers "can a possible future path even happen in time" for one market.
Deliberately conservative: this codebase has no real historical dataset of
per-step durations (how long a bill typically sits in committee, how long
between FOMC meetings a market-relevant decision typically takes), so
`estimated_minimum_path_time`/`estimated_typical_path_time` are honestly
`None`/`UNKNOWN` rather than an invented number. The one thing this module
CAN determine with certainty, requiring no duration model at all, is
whether the clock has already run out -- a plain comparison of
`time_remaining_hours` against zero -- which is what drives the
`IMPOSSIBLE` state.

`deadline_pressure`/`path_feasibility` reuse the SAME real values
world_state.py's `_derive_resolution_path` already computes (from real
step-completion counts and time remaining) rather than recomputing a
second, potentially-diverging notion of the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .next_event import NextEvent
from .world_state import ResolutionPath

EventClockFeasibility = Literal["HIGH", "MEDIUM", "LOW", "IMPOSSIBLE", "UNKNOWN"]
TimingConfidence = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


@dataclass(frozen=True)
class EventClock:
    deadline: str | None
    time_remaining_hours: float | None
    required_steps_remaining: int | None
    optional_steps_remaining: int | None
    # Honestly None/UNKNOWN whenever no real per-step duration dataset
    # exists to derive them from -- see module docstring.
    estimated_minimum_path_time_hours: float | None
    estimated_typical_path_time_hours: float | None
    schedule_slack_hours: float | None
    deadline_pressure: str
    path_feasibility: EventClockFeasibility
    blocking_step: str | None
    timing_confidence: TimingConfidence

    def as_dict(self) -> dict:
        return {
            "deadline": self.deadline,
            "time_remaining_hours": self.time_remaining_hours,
            "required_steps_remaining": self.required_steps_remaining,
            "optional_steps_remaining": self.optional_steps_remaining,
            "estimated_minimum_path_time_hours": self.estimated_minimum_path_time_hours,
            "estimated_typical_path_time_hours": self.estimated_typical_path_time_hours,
            "schedule_slack_hours": self.schedule_slack_hours,
            "deadline_pressure": self.deadline_pressure,
            "path_feasibility": self.path_feasibility,
            "blocking_step": self.blocking_step,
            "timing_confidence": self.timing_confidence,
        }


def derive_event_clock(
    deadline: str | None,
    time_remaining_hours: float | None,
    resolution_path: ResolutionPath | None,
    next_event: NextEvent | None,
) -> EventClock:
    required_steps_remaining = resolution_path.steps_remaining if resolution_path is not None else None
    optional_steps_remaining = None  # no real concept of an "optional" step exists in any template today

    deadline_pressure = resolution_path.deadline_pressure if resolution_path is not None else "UNKNOWN"

    blocking_step = (
        next_event.next_event_type.lower()
        if next_event is not None and next_event.status == "BLOCKED" and next_event.next_event_type
        else None
    )

    clock_expired = time_remaining_hours is not None and time_remaining_hours <= 0
    has_open_work = required_steps_remaining is not None and required_steps_remaining > 0

    if clock_expired and has_open_work:
        path_feasibility: EventClockFeasibility = "IMPOSSIBLE"
        timing_confidence: TimingConfidence = "HIGH"  # a plain clock comparison, no duration model needed
    elif resolution_path is None or not resolution_path.applies or required_steps_remaining is None:
        path_feasibility = "UNKNOWN"
        timing_confidence = "UNKNOWN"
    else:
        # No real per-step duration dataset exists to independently judge
        # feasibility, so this reuses world_state.py's own real
        # path_feasibility (derived from real step-completion counts and
        # time remaining) rather than inventing a second computation.
        path_feasibility = resolution_path.path_feasibility
        timing_confidence = "LOW"

    return EventClock(
        deadline=deadline,
        time_remaining_hours=time_remaining_hours,
        required_steps_remaining=required_steps_remaining,
        optional_steps_remaining=optional_steps_remaining,
        estimated_minimum_path_time_hours=None,
        estimated_typical_path_time_hours=None,
        schedule_slack_hours=None,
        deadline_pressure=deadline_pressure,
        path_feasibility=path_feasibility,
        blocking_step=blocking_step,
        timing_confidence=timing_confidence,
    )
