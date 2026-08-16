"""Phase E — Structured World State: pure composition tests. No network,
no probability computation -- only real values from already-existing
WorldState/ResolutionPath/DataGapReport objects assembled into one
compact summary."""

from __future__ import annotations

from polymarketpulse.data_gaps import DataGap, DataGapReport, GapPriority
from polymarketpulse.prediction.structured_state import assemble_structured_world_state
from polymarketpulse.prediction.world_state import (
    ResolutionPath,
    ResolutionStep,
    WaterwayHealthState,
    WorldState,
)


def _base_world_state(**overrides) -> WorldState:
    defaults = {
        "yes_condition": "YES if X happens", "no_condition": "NO otherwise", "deadline": None,
        "deadline_semantics": None, "resolution_authority": None, "time_remaining_hours": 100.0,
    }
    defaults.update(overrides)
    return WorldState(**defaults)


def test_completed_and_open_steps_split_correctly() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(
            ResolutionStep(name="introduced", status="completed", evidence=("Bill introduced in the House",)),
            ResolutionStep(name="committee", status="completed", evidence=("Cleared committee",)),
            ResolutionStep(name="house_vote", status="unknown"),
            ResolutionStep(name="senate_vote", status="unknown"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
    )
    ws = _base_world_state()
    state = assemble_structured_world_state(world_state=ws, resolution_path=path, data_gap_report=None)
    assert state.completed_steps == ("introduced", "committee")
    assert state.open_steps == ("house_vote", "senate_vote", "presidential_action")
    assert "Bill introduced in the House" in state.confirmed_facts
    assert "Cleared committee" in state.confirmed_facts


def test_no_resolution_path_gives_empty_step_lists_not_fabricated() -> None:
    ws = _base_world_state()
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=None)
    assert state.completed_steps == ()
    assert state.open_steps == ()
    assert state.blockers == ()


def test_counter_evidence_produces_disputed_fact() -> None:
    ws = _base_world_state(counter_evidence_count=3)
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=None)
    assert len(state.disputed_facts) == 1
    assert "3" in state.disputed_facts[0]


def test_zero_counter_evidence_gives_no_disputed_facts() -> None:
    ws = _base_world_state(counter_evidence_count=0)
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=None)
    assert state.disputed_facts == ()


def test_data_gaps_pass_through_and_open_questions_filtered_by_severity() -> None:
    ws = _base_world_state()
    report = DataGapReport(
        market_id="m1", question="q", total_gaps=2, critical_gaps=0, high_gaps=1, medium_gaps=1, low_gaps=0,
        gaps=(
            DataGap(category="RESOLUTION_PATH", severity="HIGH", description="High-severity gap",
                     priority=GapPriority.HIGH, impact_on_confidence=0.2, recommended_sources=()),
            DataGap(category="SOURCE_HEALTH", severity="MEDIUM", description="Medium-severity gap",
                     priority=GapPriority.MEDIUM, impact_on_confidence=0.1, recommended_sources=()),
        ),
    )
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=report)
    assert set(state.data_gaps) == {"High-severity gap", "Medium-severity gap"}
    assert state.open_questions == ("High-severity gap",)


def test_expected_events_always_empty_until_phase_f_exists() -> None:
    ws = _base_world_state()
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=None)
    assert state.expected_events == ()


def test_current_state_prefers_resolution_path_over_generic_fallback() -> None:
    path = ResolutionPath(
        applies=True, template_name="LEGISLATION",
        steps=(ResolutionStep(name="introduced", status="completed", evidence=("Bill introduced",)),),
    )
    ws = _base_world_state(evidence_for_yes_count=5, evidence_for_no_count=1)
    state = assemble_structured_world_state(world_state=ws, resolution_path=path, data_gap_report=None)
    assert "introduced" in state.current_state
    assert "LEGISLATION" in state.current_state


def test_current_state_falls_back_to_evidence_counts_when_nothing_structural() -> None:
    ws = _base_world_state(evidence_for_yes_count=3, evidence_for_no_count=2)
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=None)
    assert state.current_state is not None
    assert "3" in state.current_state and "2" in state.current_state


def test_unknown_waterway_state_never_produces_a_prose_wrapped_placeholder() -> None:
    """7.17.2 Product-Truth-Retest finding: WaterwayHealthState.current_state
    is honestly the literal string "UNKNOWN" when zero qualifying evidence
    exists (basis_evidence_count == 0) -- but the old code wrapped it into
    "Waterway-Status: UNKNOWN.", a sentence that no longer exact-matches
    product_mode.py's "UNKNOWN" placeholder check, silently promoting
    markets with genuinely zero waterway evidence to STRUCTURED_OUTLOOK
    (confirmed live on polymarket:2176262). With no other real evidence at
    all, current_state must honestly stay None -- never a disguised-UNKNOWN
    sentence -- per this module's own stated contract ("Never invents a
    state when nothing real is known")."""
    ws = _base_world_state(
        waterway_state=WaterwayHealthState(current_state="UNKNOWN", trend="UNKNOWN", basis_evidence_count=0),
        evidence_for_yes_count=0, evidence_for_no_count=0,
    )
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=None)
    assert state.current_state is None

    # With SOME real evidence present, the honest evidence-count fallback
    # must still be reachable (not blocked by the waterway UNKNOWN branch).
    ws2 = _base_world_state(
        waterway_state=WaterwayHealthState(current_state="UNKNOWN", trend="UNKNOWN", basis_evidence_count=0),
        evidence_for_yes_count=2, evidence_for_no_count=1,
    )
    state2 = assemble_structured_world_state(world_state=ws2, resolution_path=None, data_gap_report=None)
    assert state2.current_state is not None
    assert "UNKNOWN" not in state2.current_state
    assert "2 Belege für YES" in state2.current_state


def test_real_waterway_state_still_surfaces_as_real_content() -> None:
    """The fix must not suppress a genuinely known waterway state -- only
    the honest-unknown sentinel."""
    ws = _base_world_state(
        waterway_state=WaterwayHealthState(current_state="RESTRICTED", trend="WORSENING", basis_evidence_count=4),
    )
    state = assemble_structured_world_state(world_state=ws, resolution_path=None, data_gap_report=None)
    assert state.current_state == "Waterway-Status: RESTRICTED."
