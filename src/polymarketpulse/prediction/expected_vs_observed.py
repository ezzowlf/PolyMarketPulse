"""Phase H — Expected vs Observed.

Tracks whether the previously-expected next resolution step has actually
happened, and whether the currently-expected one is running late against
the market's own real deadline. Deliberately bounded to what this
codebase can determine WITHOUT a fabricated per-step calendar: Phase G
(event_clock.py) explicitly declined to invent per-step duration
estimates, so "lateness" here is derived only from the one real date this
system has -- the market's own deadline -- never a guessed expected-by
date for an individual step.

`observed_at` for the previously-completed step comes from the real
ResolutionStep.timestamp (set by world_state.py from the real dated
claim/article evidence that confirmed it) -- never invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .event_clock import EventClock
from .next_event import NextEvent
from .world_state import ResolutionPath

LatenessState = Literal["ON_TRACK", "AT_RISK", "SEVERELY_LATE", "UNKNOWN"]


@dataclass(frozen=True)
class ExpectedVsObserved:
    expected_event: str | None
    expected_by: str | None
    observed: bool | None
    observed_at: str | None
    lateness_hours: float | None
    lateness_state: LatenessState
    implication: str | None

    def as_dict(self) -> dict:
        return {
            "expected_event": self.expected_event,
            "expected_by": self.expected_by,
            "observed": self.observed,
            "observed_at": self.observed_at,
            "lateness_hours": self.lateness_hours,
            "lateness_state": self.lateness_state,
            "implication": self.implication,
        }


def derive_expected_vs_observed(
    resolution_path: ResolutionPath | None,
    next_event: NextEvent | None,
    event_clock: EventClock | None,
) -> ExpectedVsObserved:
    if resolution_path is None or not resolution_path.applies or next_event is None:
        return ExpectedVsObserved(
            expected_event=None, expected_by=None, observed=None, observed_at=None,
            lateness_hours=None, lateness_state="UNKNOWN", implication=None,
        )

    if next_event.status == "ALREADY_OCCURRED":
        # Every real, event-eligible step is confirmed -- the most recent
        # completed step IS what was most recently expected, and it has
        # now genuinely been observed.
        completed = [s for s in resolution_path.steps if s.status == "completed"]
        last = completed[-1] if completed else None
        return ExpectedVsObserved(
            expected_event=last.name.upper() if last else None,
            expected_by=event_clock.deadline if event_clock is not None else None,
            observed=True,
            observed_at=last.timestamp if last is not None else None,
            lateness_hours=None, lateness_state="ON_TRACK",
            implication="Alle bekannten Resolution-Schritte wurden beobachtet.",
        )

    expected_event = next_event.next_event_type
    expected_by = event_clock.deadline if event_clock is not None else None

    if event_clock is not None and event_clock.path_feasibility == "IMPOSSIBLE":
        lateness_hours = (
            -event_clock.time_remaining_hours
            if event_clock.time_remaining_hours is not None and event_clock.time_remaining_hours < 0
            else None
        )
        return ExpectedVsObserved(
            expected_event=expected_event, expected_by=expected_by, observed=False, observed_at=None,
            lateness_hours=lateness_hours, lateness_state="SEVERELY_LATE",
            implication=(
                f"Erwartetes Ereignis ({expected_event}) ist nicht vor der Deadline eingetreten "
                "— Path Feasibility gesunken."
            ),
        )

    if event_clock is not None and event_clock.deadline_pressure in ("HIGH", "CRITICAL"):
        return ExpectedVsObserved(
            expected_event=expected_event, expected_by=expected_by, observed=False, observed_at=None,
            lateness_hours=None, lateness_state="AT_RISK",
            implication=f"Hoher Deadline-Druck bei weiterhin ausstehendem {expected_event}.",
        )

    return ExpectedVsObserved(
        expected_event=expected_event, expected_by=expected_by, observed=False, observed_at=None,
        lateness_hours=None, lateness_state="UNKNOWN", implication=None,
    )
