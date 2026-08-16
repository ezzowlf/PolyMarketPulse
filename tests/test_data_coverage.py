"""Phase 7.7/7.8 — Critical/Optional Input Contracts, Data Coverage, and
Next Best Research Action: pure derivation tests. No network, no fetch --
every check reads an already-computed real field."""

from __future__ import annotations

from types import SimpleNamespace

from polymarketpulse.prediction.data_coverage import (
    compute_data_coverage,
    derive_next_research_action,
)


def _prediction(**overrides) -> SimpleNamespace:
    defaults = {
        "market_id": "m1",
        "forecast_archetype": "GENERIC_RESEARCH_ONLY",
        "model_diagnostics": {},
        "structured_world_state": None,
        "next_event": None,
        "event_clock": None,
        "world_state": None,
        "resolution_semantics": None,
        "independent_evidence": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_no_archetype_gives_honest_zero_coverage_and_none_action() -> None:
    prediction = _prediction()
    coverage = compute_data_coverage(prediction)
    assert coverage.archetype is None
    assert coverage.critical_total == 0
    assert coverage.coverage_ratio is None
    action = derive_next_research_action(prediction, coverage)
    assert action["action_type"] == "NONE"
    assert action["reason"] == "NO_ARCHETYPE"


def test_fed_contract_has_exactly_one_critical_input_matching_the_real_model() -> None:
    """The Fed champion model's own feature_list is ["previous_fomc_action"]
    -- the contract must not invent additional critical inputs (e.g. news
    articles) that the real model doesn't actually use."""
    prediction = _prediction(
        forecast_archetype="MACRO_POLICY",
        model_diagnostics={"prior_action": "UNCHANGED"},
    )
    coverage = compute_data_coverage(prediction)
    assert coverage.archetype == "MACRO_POLICY"
    assert coverage.critical_total == 1
    assert coverage.critical_available == 1
    assert coverage.coverage_ratio == 1.0
    assert coverage.blocking_inputs == ()
    action = derive_next_research_action(prediction, coverage)
    assert action["action_type"] == "NONE"
    assert action["reason"] == "COVERAGE_COMPLETE"


def test_fed_contract_reports_missing_prior_action_honestly() -> None:
    prediction = _prediction(forecast_archetype="MACRO_POLICY", model_diagnostics={})
    coverage = compute_data_coverage(prediction)
    assert coverage.critical_available == 0
    assert coverage.critical_failed == 1
    assert coverage.coverage_ratio == 0.0
    assert "FOMC" in coverage.blocking_inputs[0] or "fomc" in coverage.blocking_inputs[0].lower()


def test_legislation_contract_clarity_reference_case() -> None:
    """Clarity Act reference: 3 completed steps (bill identity + current
    stage known), a real next_event, but no real deadline/schedule signal
    in this fixture -- honest partial coverage, not fabricated full
    coverage."""
    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(
            resolution_path=SimpleNamespace(template_name="LEGISLATION")
        ),
        deadline=None,
    )
    sws = SimpleNamespace(
        confirmed_facts=("Clarity Act officially passed the House.",),
        completed_steps=("introduced", "committee", "house_vote"),
        open_steps=("senate_vote", "presidential_action"),
    )
    next_event = SimpleNamespace(next_event_type="SENATE_VOTE", expected_time_window=None)
    event_clock = SimpleNamespace(deadline=None, required_steps_remaining=2)

    prediction = _prediction(
        forecast_archetype="LEGISLATIVE_PROCESS",
        world_state=world_state, structured_world_state=sws,
        next_event=next_event, event_clock=event_clock,
    )
    coverage = compute_data_coverage(prediction)
    assert coverage.archetype == "LEGISLATION"
    assert coverage.critical_total == 4
    # bill_identity, official_current_stage, next_required_step available;
    # resolution_deadline missing (no real deadline in this fixture)
    assert coverage.critical_available == 3
    assert "Auflösungsfrist" in coverage.blocking_inputs[0]

    action = derive_next_research_action(prediction, coverage)
    assert action["action_type"] == "FETCH"
    assert action["reason"] == "CRITICAL_INPUT_MISSING:resolution_deadline"


def test_legislation_contract_full_coverage_when_deadline_present() -> None:
    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="LEGISLATION")),
        deadline="2027-01-01",
    )
    sws = SimpleNamespace(
        confirmed_facts=("x",), completed_steps=("introduced", "committee", "house_vote"), open_steps=(),
    )
    next_event = SimpleNamespace(next_event_type="SENATE_VOTE", expected_time_window=None)
    event_clock = SimpleNamespace(deadline="2027-01-01", required_steps_remaining=2)
    prediction = _prediction(
        forecast_archetype="LEGISLATIVE_PROCESS", world_state=world_state,
        structured_world_state=sws, next_event=next_event, event_clock=event_clock,
    )
    coverage = compute_data_coverage(prediction)
    assert coverage.critical_available == coverage.critical_total == 4
    assert coverage.coverage_ratio == 1.0


def test_geopolitics_contract_hormuz_reference_case() -> None:
    """Hormuz reference: real resolution_semantics + a real quantitative
    PortWatch claim (independent_evidence.available=True), but no second
    independent confirmation -- honest partial optional coverage."""
    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="GEOPOLITICS")),
        most_recent_evidence_published_at="2026-08-09T00:00:00+00:00",
        evidence_for_yes_count=0, evidence_for_no_count=1,
    )
    sws = SimpleNamespace(current_state="Resolution-Pfad (GEOPOLITICS) noch ohne bestätigten Schritt.")
    independent_evidence = SimpleNamespace(available=True)
    prediction = _prediction(
        forecast_archetype="GEOPOLITICS_STRATEGIC", world_state=world_state,
        structured_world_state=sws, resolution_semantics=object(),
        independent_evidence=independent_evidence,
    )
    coverage = compute_data_coverage(prediction)
    assert coverage.archetype == "GEOPOLITICS"
    assert coverage.critical_total == 4
    assert coverage.critical_available == 4  # semantics, source, observation, freshness all real
    assert coverage.optional_total == 1
    assert coverage.optional_available == 0  # only 1 evidence item, needs >=2 for independent_confirmation
    action = derive_next_research_action(prediction, coverage)
    assert action["action_type"] == "NONE"
    assert action["reason"] == "COVERAGE_COMPLETE"  # optional gaps don't block the action
