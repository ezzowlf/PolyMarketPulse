from polymarketpulse.prediction.conditional_transitions import derive_conditional_transitions
from polymarketpulse.prediction.event_clock import EventClock
from polymarketpulse.prediction.scenario_tree import derive_scenario_tree
from polymarketpulse.prediction.structured_state import StructuredWorldState
from polymarketpulse.prediction.world_state import ResolutionPath, ResolutionStep


def _clarity_path() -> ResolutionPath:
    return ResolutionPath(
        applies=True,
        template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
            ResolutionStep(name="house_vote", status="completed", evidence=("House roll-call",)),
            ResolutionStep(name="senate_vote", status="unknown"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
        blockers=("senate schedule unknown",),
    )


def test_no_real_path_has_no_tree() -> None:
    assert derive_scenario_tree(
        resolution_path=None,
        structured_world_state=None,
        conditional_transitions=(),
        event_clock=None,
    ) is None


def test_clarity_act_tree_is_derived_from_remaining_transitions() -> None:
    path = _clarity_path()
    clock = EventClock(
        deadline="2026-12-31T23:59:59Z",
        time_remaining_hours=100.0,
        required_steps_remaining=2,
        optional_steps_remaining=None,
        estimated_minimum_path_time_hours=None,
        estimated_typical_path_time_hours=None,
        schedule_slack_hours=None,
        deadline_pressure="LOW",
        path_feasibility="HIGH",
        blocking_step=None,
        timing_confidence="LOW",
    )
    tree = derive_scenario_tree(
        resolution_path=path,
        structured_world_state=StructuredWorldState(
            current_state="House passed",
            completed_steps=("introduced", "committee", "house_vote"),
            open_steps=("senate_vote", "presidential_action"),
            blockers=("senate schedule unknown",),
        ),
        conditional_transitions=derive_conditional_transitions(path),
        event_clock=clock,
        contradicting_claims=("Senate postpones consideration",),
    )
    assert tree is not None
    assert tree.template_name == "LEGISLATION"
    assert [branch.event for branch in tree.branches] == [
        "House passed",
        "SENATE_VOTE",
        "SENATE_VOTE_FAILS",
        "PRESIDENTIAL_ACTION",
        "PRESIDENTIAL_ACTION_FAILS",
        "NO_ACTION_BEFORE_DEADLINE",
    ]
    assert tree.branches[1].prerequisites == ("HOUSE_VOTE",)
    assert tree.branches[3].parent_id == "step-1-success"
    assert tree.branches[3].outcome == "YES"
    assert tree.branches[2].outcome == "NO"
    assert tree.branches[-1].expected_by == clock.deadline
    assert all(branch.probability is None for branch in tree.branches)


def test_tree_serializes_without_inventing_probability_or_timing() -> None:
    path = _clarity_path()
    tree = derive_scenario_tree(
        resolution_path=path,
        structured_world_state=None,
        conditional_transitions=derive_conditional_transitions(path),
        event_clock=None,
    )
    payload = tree.as_dict()
    assert payload["root_id"] == "current"
    assert "deadline-no-action" not in {branch["branch_id"] for branch in payload["branches"]}
    assert all(branch["probability"] is None for branch in payload["branches"])
    assert all(branch["expected_by"] is None for branch in payload["branches"])
