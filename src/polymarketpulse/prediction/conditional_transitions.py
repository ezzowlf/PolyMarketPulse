"""Phase I — Conditional Transition Engine.

Models the remaining resolution path as a sequence of state transitions
(prerequisite -> next step) rather than treating a multi-step market as a
single opaque YES/NO question. This codebase has NO real historical
dataset of per-step transition rates (e.g. "of N bills that passed the
House, M later passed the Senate") -- the honest response, per explicit
instruction, is `calibration_state="QUALITATIVE_ONLY"` and
`conditional_probability=None` for every transition, never a fabricated
percentage. This is infrastructure for a future round to populate with
real empirical rates once such a dataset exists (real resolved-market
history via retrospective learning, Phase Z) -- the shape is real today,
the numbers are honestly absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .next_event import _NON_EVENT_STEPS
from .world_state import ResolutionPath

TransitionUncertainty = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
CalibrationState = Literal["QUALITATIVE_ONLY", "CALIBRATED"]


@dataclass(frozen=True)
class ConditionalTransition:
    prerequisite_state: str
    transition: str
    conditional_probability: float | None
    uncertainty: TransitionUncertainty
    empirical_basis: str
    source: str | None
    sample_size: int | None
    calibration_state: CalibrationState

    def as_dict(self) -> dict:
        return {
            "prerequisite_state": self.prerequisite_state,
            "transition": self.transition,
            "conditional_probability": self.conditional_probability,
            "uncertainty": self.uncertainty,
            "empirical_basis": self.empirical_basis,
            "source": self.source,
            "sample_size": self.sample_size,
            "calibration_state": self.calibration_state,
        }


def derive_conditional_transitions(resolution_path: ResolutionPath | None) -> tuple[ConditionalTransition, ...]:
    """One transition per remaining (not-yet-completed) real step, chained
    to its real prerequisite (the immediately preceding step, or
    "CURRENT_STATE" for the first one). Empty tuple when there is no real
    multi-step path, or every real step is already complete -- nothing
    left to model a transition for."""
    if resolution_path is None or not resolution_path.applies:
        return ()

    steps = resolution_path.steps
    remaining_indices = [i for i, s in enumerate(steps) if s.status != "completed"]
    if not remaining_indices:
        return ()

    transitions: list[ConditionalTransition] = []
    for i in remaining_indices:
        if steps[i].name in _NON_EVENT_STEPS:
            continue  # a state descriptor, not a transition target -- see next_event.py
        prerequisite = steps[i - 1].name.upper() if i > 0 else "CURRENT_STATE"
        transitions.append(
            ConditionalTransition(
                prerequisite_state=prerequisite,
                transition=steps[i].name.upper(),
                conditional_probability=None,
                uncertainty="UNKNOWN",
                empirical_basis="NONE",
                source=None,
                sample_size=None,
                calibration_state="QUALITATIVE_ONLY",
            )
        )
    return tuple(transitions)
