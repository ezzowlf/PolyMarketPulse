"""Phase I — Conditional Transition Engine: pure derivation tests. No
fabricated conditional probabilities -- every transition must be
QUALITATIVE_ONLY given this codebase has no real per-step transition-
rate dataset."""

from __future__ import annotations

from polymarketpulse.prediction.conditional_transitions import derive_conditional_transitions
from polymarketpulse.prediction.world_state import ResolutionPath, ResolutionStep


def test_no_resolution_path_gives_empty_tuple() -> None:
    assert derive_conditional_transitions(None) == ()


def test_not_applying_path_gives_empty_tuple() -> None:
    path = ResolutionPath(applies=False, template_name="GENERIC")
    assert derive_conditional_transitions(path) == ()


def test_all_completed_gives_empty_tuple() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
        ),
    )
    assert derive_conditional_transitions(path) == ()


def test_clarity_act_reference_case_chains_remaining_transitions() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
            ResolutionStep(name="house_vote", status="completed"),
            ResolutionStep(name="senate_vote", status="unknown"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
    )
    transitions = derive_conditional_transitions(path)
    assert len(transitions) == 2
    assert transitions[0].prerequisite_state == "HOUSE_VOTE"
    assert transitions[0].transition == "SENATE_VOTE"
    assert transitions[1].prerequisite_state == "SENATE_VOTE"
    assert transitions[1].transition == "PRESIDENTIAL_ACTION"
    for t in transitions:
        assert t.conditional_probability is None
        assert t.calibration_state == "QUALITATIVE_ONLY"
        assert t.empirical_basis == "NONE"


def test_first_transition_prerequisite_is_current_state_when_nothing_completed_yet() -> None:
    path = ResolutionPath(
        applies=True, template_name="GEOPOLITICS",
        steps=(
            ResolutionStep(name="current_state", status="unknown"),
            ResolutionStep(name="escalation", status="unknown"),
        ),
    )
    transitions = derive_conditional_transitions(path)
    assert transitions[0].prerequisite_state == "CURRENT_STATE"
