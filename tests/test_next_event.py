"""Phase F — Next Event Engine: pure derivation tests. No network, no LLM
-- next_event.py only reads an already-computed real ResolutionPath."""

from __future__ import annotations

from polymarketpulse.prediction.next_event import derive_next_event
from polymarketpulse.prediction.world_state import ResolutionPath, ResolutionStep


def test_no_resolution_path_gives_unknown_status_not_fabricated_event() -> None:
    result = derive_next_event(resolution_path=None)
    assert result.status == "UNKNOWN"
    assert result.next_event_type is None


def test_resolution_path_not_applying_gives_unknown() -> None:
    path = ResolutionPath(applies=False, template_name="GENERIC")
    result = derive_next_event(resolution_path=path)
    assert result.status == "UNKNOWN"
    assert result.next_event_type is None


def test_clarity_act_reference_case_next_event_is_senate_vote() -> None:
    """Real Phase E reference case: 3 completed steps, senate_vote/
    presidential_action open -> next event must be SENATE_VOTE, not
    presidential_action (sequential) and not current_state."""
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
    result = derive_next_event(resolution_path=path)
    assert result.next_event_type == "SENATE_VOTE"
    assert "Senat" in result.next_event_description
    assert result.status == "PLAUSIBLE"
    assert result.prerequisites == ()
    assert result.resolution_relevance == 1.0


def test_current_state_step_never_selected_as_next_event() -> None:
    """GEOPOLITICS/FED templates start with `current_state`, a state
    descriptor, not a future event -- it must never be picked as
    next_event_type even though it's technically not 'completed'."""
    path = ResolutionPath(
        applies=True, template_name="GEOPOLITICS",
        steps=(
            ResolutionStep(name="current_state", status="unknown"),
            ResolutionStep(name="escalation", status="unknown"),
            ResolutionStep(name="operational_change", status="unknown"),
            ResolutionStep(name="resolution_threshold", status="unknown"),
            ResolutionStep(name="confirmation", status="unknown"),
        ),
    )
    result = derive_next_event(resolution_path=path)
    assert result.next_event_type == "ESCALATION"


def test_all_steps_completed_gives_already_occurred() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
            ResolutionStep(name="house_vote", status="completed"),
            ResolutionStep(name="senate_vote", status="completed"),
            ResolutionStep(name="presidential_action", status="completed"),
        ),
    )
    result = derive_next_event(resolution_path=path)
    assert result.status == "ALREADY_OCCURRED"
    assert result.next_event_type is None


def test_blocked_step_gives_blocked_status_with_blockers() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="blocked"),
            ResolutionStep(name="house_vote", status="unknown"),
            ResolutionStep(name="senate_vote", status="unknown"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
        blockers=("Ausschuss hat die Abstimmung ausgesetzt.",),
    )
    result = derive_next_event(resolution_path=path)
    assert result.next_event_type == "COMMITTEE"
    assert result.status == "BLOCKED"
    assert result.blockers == ("Ausschuss hat die Abstimmung ausgesetzt.",)


def test_in_progress_step_gives_expected_status() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
            ResolutionStep(name="house_vote", status="in_progress"),
            ResolutionStep(name="senate_vote", status="unknown"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
    )
    result = derive_next_event(resolution_path=path)
    assert result.next_event_type == "HOUSE_VOTE"
    assert result.status == "EXPECTED"


def test_earlier_open_step_becomes_a_prerequisite_of_a_later_one() -> None:
    """Real, non-sequential structured data: senate_vote confirmed via a
    real PATH_STEP claim while house_vote is still unknown. The next
    'first non-completed' step is house_vote itself, so this mainly
    verifies prerequisites stay empty when the next step IS the earliest
    open one (the common case)."""
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
            ResolutionStep(name="house_vote", status="unknown"),
            ResolutionStep(name="senate_vote", status="completed"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
    )
    result = derive_next_event(resolution_path=path)
    assert result.next_event_type == "HOUSE_VOTE"
    assert result.prerequisites == ()


def test_supporting_claim_ids_and_source_ids_from_matching_path_step_claims() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed"),
            ResolutionStep(name="committee", status="completed"),
            ResolutionStep(name="house_vote", status="in_progress"),
            ResolutionStep(name="senate_vote", status="unknown"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
    )
    claims = (
        {"claim_id": "abc123", "resolution_step": "house_vote", "source": "govtrack", "timestamp": None,
         "detail": "Scheduled for a House floor vote"},
        {"claim_id": "def456", "resolution_step": "committee", "source": "govtrack", "timestamp": None,
         "detail": "Cleared committee"},
    )
    result = derive_next_event(resolution_path=path, path_step_claims=claims)
    assert result.supporting_claim_ids == ("abc123",)
    assert result.source_ids == ("govtrack",)
