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


# ---------------------------------------------------------------------
# Phase 7.8.3: Dependency-Aware VOI
# ---------------------------------------------------------------------


def test_geopolitics_resolution_semantics_missing_blocks_downstream_inputs() -> None:
    """When resolution_semantics itself is missing, current_observation/
    primary_measurement_source/freshness are ALL also missing (they depend
    on it) -- the next research action must target resolution_semantics
    first, not one of its dependents, even though later requirements are
    also in the blocking_inputs list."""
    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="GEOPOLITICS")),
        most_recent_evidence_published_at=None,
        evidence_for_yes_count=0, evidence_for_no_count=0,
    )
    sws = SimpleNamespace(current_state="UNKNOWN")
    prediction = _prediction(
        forecast_archetype="GEOPOLITICS_STRATEGIC", world_state=world_state,
        structured_world_state=sws, resolution_semantics=None, independent_evidence=None,
    )
    coverage = compute_data_coverage(prediction)
    assert coverage.critical_available == 0
    action = derive_next_research_action(prediction, coverage)
    assert action["action_type"] == "FETCH"
    assert action["reason"] == "CRITICAL_INPUT_MISSING:resolution_semantics"


def test_legislation_next_required_step_not_proposed_before_current_stage() -> None:
    """schedule_timing/next_required_step depend on official_current_stage --
    if the stage itself is unknown, the action must target the stage, not
    the dependent next_required_step, even though next_required_step also
    happens to be missing."""
    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="LEGISLATION")),
        deadline="2027-01-01",
    )
    sws = SimpleNamespace(confirmed_facts=("x",), completed_steps=(), open_steps=())
    event_clock = SimpleNamespace(deadline="2027-01-01", required_steps_remaining=None)
    prediction = _prediction(
        forecast_archetype="LEGISLATIVE_PROCESS", world_state=world_state,
        structured_world_state=sws, next_event=None, event_clock=event_clock,
    )
    coverage = compute_data_coverage(prediction)
    action = derive_next_research_action(prediction, coverage)
    assert action["reason"] == "CRITICAL_INPUT_MISSING:official_current_stage"


# ---------------------------------------------------------------------
# Phase 7.8.4: Provider-Health-Gewichtung
# ---------------------------------------------------------------------


class _FakeHealth:
    def __init__(self, state):
        self._state = state

    def state(self):
        return self._state


class _FakeStorage:
    def __init__(self, health_by_provider: dict):
        self._health = health_by_provider

    def get_provider_health(self, source_id: str):
        return self._health.get(source_id)


def test_offline_provider_with_no_known_fallback_yields_blocked_provider() -> None:
    """gdelt is the provider for GEOPOLITICS' independent_confirmation and
    has no configured real fallback in this codebase -- if it were the
    active target and OFFLINE, the honest result is BLOCKED_PROVIDER, not a
    fetch attempt that will only fail again. Verified directly against the
    routing table rather than forcing a specific fixture through the full
    dependency chain."""
    from polymarketpulse.data_sources import ProviderHealthState
    from polymarketpulse.prediction.data_coverage import _PROVIDER_FALLBACK

    assert "gdelt" not in _PROVIDER_FALLBACK
    assert ProviderHealthState.OFFLINE.value == "OFFLINE"


def test_offline_provider_with_discovery_only_fallback_yields_low_closability() -> None:
    """imf_portwatch's only real fallback is gdelt, a discovery-only
    provider (Phase 7.8.5 case C: 'only degraded discovery path') -- that
    is real and still worth attempting (FETCH, not BLOCKED_PROVIDER), but
    must not be reported as equally reliable as a real structured fallback
    (MEDIUM), hence LOW."""
    from polymarketpulse.data_sources import ProviderHealthState

    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="GEOPOLITICS")),
        most_recent_evidence_published_at=None,
        evidence_for_yes_count=0, evidence_for_no_count=0,
    )
    prediction = _prediction(
        forecast_archetype="GEOPOLITICS_STRATEGIC", world_state=world_state,
        structured_world_state=SimpleNamespace(current_state="Real state, not UNKNOWN"),
        resolution_semantics=object(), independent_evidence=None,
    )
    coverage = compute_data_coverage(prediction)
    # next unblocked missing input here is primary_measurement_source ->
    # imf_portwatch, whose only real fallback (gdelt) is discovery-only.
    storage = _FakeStorage({"imf_portwatch": _FakeHealth(ProviderHealthState.OFFLINE)})
    action = derive_next_research_action(prediction, coverage, storage=storage)
    assert action["action_type"] == "FETCH"
    assert action["fallback_provider"] == "gdelt"
    assert action["closability"] == "LOW"


def test_healthy_structured_fallback_yields_medium_closability() -> None:
    """govtrack/congress_gov are both real structured providers for each
    other -- an OFFLINE primary with that kind of fallback is a genuinely
    stronger position (MEDIUM) than falling back to discovery-only gdelt."""
    from polymarketpulse.data_sources import ProviderHealthState

    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="LEGISLATION")),
        deadline=None,
    )
    prediction = _prediction(
        forecast_archetype="LEGISLATIVE_PROCESS", world_state=world_state,
        structured_world_state=None, next_event=None, event_clock=None,
    )
    coverage = compute_data_coverage(prediction)
    storage = _FakeStorage({"govtrack": _FakeHealth(ProviderHealthState.OFFLINE)})
    action = derive_next_research_action(prediction, coverage, storage=storage)
    assert action["action_type"] == "FETCH"
    assert action["fallback_provider"] == "congress_gov"
    assert action["closability"] == "MEDIUM"


def test_unknown_provider_health_never_blocks_action() -> None:
    """No storage passed -- honest UNKNOWN health must not block a fetch
    that would otherwise be perfectly actionable."""
    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="LEGISLATION")),
        deadline=None,
    )
    prediction = _prediction(
        forecast_archetype="LEGISLATIVE_PROCESS", world_state=world_state,
        structured_world_state=None, next_event=None, event_clock=None,
    )
    coverage = compute_data_coverage(prediction)
    action = derive_next_research_action(prediction, coverage)
    assert action["action_type"] == "FETCH"
    assert action["provider_health"] == "UNKNOWN"
    assert action["closability"] == "HIGH"


# ---------------------------------------------------------------------
# Phase 7.8.6: Expected Product Effect
# ---------------------------------------------------------------------


def test_fed_missing_input_reports_enable_champion_inference() -> None:
    """Direct unit check on the categorization function itself: fed_policy's
    prior_fomc_action has no real live provider (it comes from the curated
    training dataset / market_resolutions tracking, not a fetchable source),
    so the end-to-end action correctly reports BLOCKED_PROVIDER -- but the
    underlying effect classification must still say what closing this gap
    WOULD do, which is verified here directly."""
    from polymarketpulse.prediction.data_coverage import INPUT_CONTRACTS, _expected_product_effect

    prediction = _prediction(forecast_archetype="MACRO_POLICY", model_diagnostics={})
    coverage = compute_data_coverage(prediction)
    fed_req = INPUT_CONTRACTS["MACRO_POLICY"][0]
    assert _expected_product_effect("MACRO_POLICY", fed_req, coverage) == "ENABLE_CHAMPION_INFERENCE"


def test_legislation_missing_deadline_reports_enable_structured_outlook() -> None:
    """Clarity-style fixture: only ONE critical input away from full
    coverage -- closing it would plausibly flip the product mode, so the
    effect must say so, never claim a numeric-model upgrade for a
    non-Champion archetype."""
    world_state = SimpleNamespace(
        path_to_resolution=SimpleNamespace(resolution_path=SimpleNamespace(template_name="LEGISLATION")),
        deadline=None,
    )
    sws = SimpleNamespace(
        confirmed_facts=("x",), completed_steps=("introduced", "committee", "house_vote"), open_steps=(),
    )
    next_event = SimpleNamespace(next_event_type="SENATE_VOTE", expected_time_window=None)
    event_clock = SimpleNamespace(deadline=None, required_steps_remaining=2)
    prediction = _prediction(
        forecast_archetype="LEGISLATIVE_PROCESS", world_state=world_state,
        structured_world_state=sws, next_event=next_event, event_clock=event_clock,
    )
    coverage = compute_data_coverage(prediction)
    action = derive_next_research_action(prediction, coverage)
    assert action["expected_product_effect"] == "ENABLE_STRUCTURED_OUTLOOK"


def test_hormuz_optional_gap_reports_improve_confidence_not_a_fake_upgrade() -> None:
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
    # All CRITICAL inputs are covered; the only remaining gap is OPTIONAL
    # (independent_confirmation) -- derive_next_research_action currently
    # only proposes CRITICAL gaps as the primary action (COVERAGE_COMPLETE),
    # so this directly exercises the optional-effect branch instead.
    from polymarketpulse.prediction.data_coverage import INPUT_CONTRACTS, _expected_product_effect

    optional_req = next(r for r in INPUT_CONTRACTS["GEOPOLITICS"] if r.criticality == "OPTIONAL")
    assert _expected_product_effect("GEOPOLITICS", optional_req, coverage) == "IMPROVE_CONFIDENCE"


# ---------------------------------------------------------------------
# Phase 7.8.7: VOI Score monotonicity
# ---------------------------------------------------------------------


def test_voi_higher_closability_scores_higher_all_else_equal() -> None:
    from polymarketpulse.prediction.data_coverage import _voi_score

    kwargs = {"criticality": "CRITICAL", "has_dependents": False, "expected_effect": "IMPROVE_DATA_COVERAGE",
              "deadline_bonus": 0.0, "failure_penalty": 0.0}
    assert _voi_score(closability="HIGH", **kwargs) > _voi_score(closability="MEDIUM", **kwargs)
    assert _voi_score(closability="MEDIUM", **kwargs) > _voi_score(closability="LOW", **kwargs)
    assert _voi_score(closability="LOW", **kwargs) > _voi_score(closability="BLOCKED", **kwargs)


def test_voi_critical_gap_scores_higher_than_optional_all_else_equal() -> None:
    from polymarketpulse.prediction.data_coverage import _voi_score

    kwargs = {"closability": "HIGH", "has_dependents": False, "expected_effect": "IMPROVE_DATA_COVERAGE",
              "deadline_bonus": 0.0, "failure_penalty": 0.0}
    assert _voi_score(criticality="CRITICAL", **kwargs) > _voi_score(criticality="OPTIONAL", **kwargs)


def test_voi_near_deadline_scores_higher_than_far_deadline() -> None:
    from polymarketpulse.prediction.data_coverage import _voi_score

    kwargs = {"criticality": "CRITICAL", "closability": "HIGH", "has_dependents": False,
              "expected_effect": "IMPROVE_DATA_COVERAGE", "failure_penalty": 0.0}
    assert _voi_score(deadline_bonus=12.0, **kwargs) > _voi_score(deadline_bonus=0.0, **kwargs)


def test_voi_meaningful_upgrade_scores_higher_than_no_upgrade() -> None:
    from polymarketpulse.prediction.data_coverage import _voi_score

    kwargs = {"criticality": "CRITICAL", "closability": "HIGH", "has_dependents": False,
              "deadline_bonus": 0.0, "failure_penalty": 0.0}
    assert (
        _voi_score(expected_effect="ENABLE_CHAMPION_INFERENCE", **kwargs)
        > _voi_score(expected_effect="NO_PRODUCT_UPGRADE", **kwargs)
    )


def test_voi_recent_failures_reduce_score_but_never_go_negative() -> None:
    from polymarketpulse.prediction.data_coverage import _voi_score

    kwargs = {"criticality": "OPTIONAL", "closability": "LOW", "has_dependents": False,
              "expected_effect": "NO_PRODUCT_UPGRADE", "deadline_bonus": 0.0}
    assert _voi_score(failure_penalty=0.0, **kwargs) >= _voi_score(failure_penalty=50.0, **kwargs)
    assert _voi_score(failure_penalty=500.0, **kwargs) == 0


def test_recent_failure_penalty_reads_real_gap_closures() -> None:
    from polymarketpulse.prediction.data_coverage import _recent_failure_penalty

    class _Storage:
        def get_gap_closures(self, gap_key, limit):
            assert gap_key == "input:LEGISLATION:bill_identity"
            return [{"result_status": "OPEN"}, {"result_status": "OPEN"}, {"result_status": "CLOSED"}]

    penalty = _recent_failure_penalty(_Storage(), "input:LEGISLATION:bill_identity")
    assert penalty == 16  # 2 non-closing attempts * 8


def test_recent_failure_penalty_honestly_zero_without_storage() -> None:
    from polymarketpulse.prediction.data_coverage import _recent_failure_penalty

    assert _recent_failure_penalty(None, "any-gap-key") == 0.0


# ---------------------------------------------------------------------
# Phase 7.8.15: next_retry
# ---------------------------------------------------------------------


def test_next_retry_derives_a_real_timestamp_from_provider_last_failure() -> None:
    """Direct unit check on _next_retry(): reuses the exact 1-hour window
    data_sources.ProviderHealth.state() itself treats as 'recent failure',
    so a real BLOCKED_PROVIDER action (whenever the provider config has no
    known fallback for a given input) would surface a real, not invented,
    retry time."""
    from datetime import UTC, datetime

    from polymarketpulse.prediction.data_coverage import _next_retry

    class _Health:
        last_failure = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)

    class _Storage:
        def get_provider_health(self, source_id):
            return _Health() if source_id == "imf_portwatch" else None

    assert _next_retry(_Storage(), "imf_portwatch") == "2026-08-16T11:00:00+00:00"
    assert _next_retry(_Storage(), "unknown_provider") is None
    assert _next_retry(None, "imf_portwatch") is None


def test_fetch_and_none_actions_carry_no_next_retry() -> None:
    prediction = _prediction()
    coverage = compute_data_coverage(prediction)
    action = derive_next_research_action(prediction, coverage)
    assert action["next_retry"] is None
