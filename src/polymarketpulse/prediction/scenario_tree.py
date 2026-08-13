"""Phase J — deterministic scenario tree derived from the real resolution path.

This is an explanatory projection, not a second world model.  It only uses
the already-computed ResolutionPath, StructuredWorldState, EventClock and
ConditionalTransition objects.  In particular, probabilities remain None
until a transition has a calibrated empirical basis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .conditional_transitions import ConditionalTransition
from .event_clock import EventClock
from .structured_state import StructuredWorldState
from .world_state import ResolutionPath

BranchOutcome = Literal["YES", "NO", "UNRESOLVED"]


@dataclass(frozen=True)
class ScenarioBranch:
    branch_id: str
    parent_id: str | None
    event: str
    branch_type: Literal["CURRENT", "SUCCESS", "FAILURE", "DEADLINE"]
    prerequisites: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    supporting_claims: tuple[str, ...] = field(default_factory=tuple)
    contradicting_claims: tuple[str, ...] = field(default_factory=tuple)
    expected_by: str | None = None
    outcome: BranchOutcome = "UNRESOLVED"
    probability: float | None = None
    calibration_state: str = "QUALITATIVE_ONLY"

    def as_dict(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "parent_id": self.parent_id,
            "event": self.event,
            "branch_type": self.branch_type,
            "prerequisites": list(self.prerequisites),
            "blockers": list(self.blockers),
            "supporting_claims": list(self.supporting_claims),
            "contradicting_claims": list(self.contradicting_claims),
            "expected_by": self.expected_by,
            "outcome": self.outcome,
            "probability": self.probability,
            "calibration_state": self.calibration_state,
        }


@dataclass(frozen=True)
class ScenarioTree:
    root_id: str
    template_name: str
    branches: tuple[ScenarioBranch, ...]

    def as_dict(self) -> dict:
        return {
            "root_id": self.root_id,
            "template_name": self.template_name,
            "branches": [branch.as_dict() for branch in self.branches],
        }


def derive_scenario_tree(
    *,
    resolution_path: ResolutionPath | None,
    structured_world_state: StructuredWorldState | None,
    conditional_transitions: tuple[ConditionalTransition, ...],
    event_clock: EventClock | None,
    contradicting_claims: tuple[str, ...] = (),
) -> ScenarioTree | None:
    """Build success/failure branches for every remaining real transition."""
    if resolution_path is None or not resolution_path.applies or not conditional_transitions:
        return None

    root_id = "current"
    current = structured_world_state.current_state if structured_world_state else None
    branches: list[ScenarioBranch] = [
        ScenarioBranch(
            branch_id=root_id,
            parent_id=None,
            event=current or "CURRENT_STATE",
            branch_type="CURRENT",
        )
    ]
    success_parent = root_id
    path_blockers = structured_world_state.blockers if structured_world_state else resolution_path.blockers
    step_by_name = {step.name.upper(): step for step in resolution_path.steps}

    for index, transition in enumerate(conditional_transitions, start=1):
        success_id = f"step-{index}-success"
        is_terminal = index == len(conditional_transitions)
        step = step_by_name.get(transition.transition)
        support = step.evidence if step is not None else ()
        branches.append(
            ScenarioBranch(
                branch_id=success_id,
                parent_id=success_parent,
                event=transition.transition,
                branch_type="SUCCESS",
                prerequisites=(transition.prerequisite_state,),
                blockers=tuple(path_blockers),
                supporting_claims=tuple(support),
                contradicting_claims=contradicting_claims,
                expected_by=None,
                outcome="YES" if is_terminal else "UNRESOLVED",
                probability=transition.conditional_probability,
                calibration_state=transition.calibration_state,
            )
        )
        branches.append(
            ScenarioBranch(
                branch_id=f"step-{index}-failure",
                parent_id=success_parent,
                event=f"{transition.transition}_FAILS",
                branch_type="FAILURE",
                prerequisites=(transition.prerequisite_state,),
                blockers=tuple(path_blockers),
                supporting_claims=contradicting_claims,
                contradicting_claims=tuple(support),
                outcome="NO",
            )
        )
        success_parent = success_id

    if event_clock is not None and event_clock.deadline is not None:
        branches.append(
            ScenarioBranch(
                branch_id="deadline-no-action",
                parent_id=root_id,
                event="NO_ACTION_BEFORE_DEADLINE",
                branch_type="DEADLINE",
                blockers=tuple(path_blockers),
                expected_by=event_clock.deadline,
                outcome="NO",
            )
        )
    return ScenarioTree(root_id=root_id, template_name=resolution_path.template_name, branches=tuple(branches))
