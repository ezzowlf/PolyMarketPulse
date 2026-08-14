from datetime import date
from pathlib import Path

from polymarketpulse.prediction.archetypes import route_archetype
from polymarketpulse.prediction.engine import compute_prediction
from polymarketpulse.prediction.fed_policy import (
    OUTCOMES,
    parse_fed_target,
    predict_shadow,
    registry_records,
    training_dataset,
    validate_model,
)
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.providers.fedboard import FedPolicyDecision
from polymarketpulse.providers.fred import MacroSnapshot
from polymarketpulse.storage import Storage

QUESTION = "Will the Fed increase interest rates by 25 bps after the September 2025 meeting?"
RULE = "The upper bound of the target federal funds range changes by exactly 25 basis points."


def _snapshot(as_of: date) -> MacroSnapshot:
    return MacroSnapshot(
        policy_rate=4.5, policy_rate_as_of=as_of, cpi_yoy=2.5, cpi_yoy_prior=2.7,
        unemployment_rate=4.1, unemployment_rate_prior=4.0, as_of_date=as_of,
        next_fomc_meeting_date=date(2025, 9, 17),
    )


def _policy(as_of: date = date(2025, 7, 30)) -> FedPolicyDecision:
    return FedPolicyDecision("UNCHANGED", as_of, 4.25, 4.5, "https://fed.example/statement", "2025-07-30T00:00:00+00:00")


def test_fed_dataset_is_real_meeting_level_and_time_ordered() -> None:
    dataset = training_dataset()
    assert len(dataset) == 40
    assert [row.meeting_date for row in dataset] == sorted(row.meeting_date for row in dataset)
    assert {row.action for row in dataset} <= set(OUTCOMES)


def test_fed_target_requires_exact_bucket() -> None:
    assert parse_fed_target(QUESTION, RULE).outcome == "HIKE_25"
    assert not parse_fed_target("Will the Fed change rates?", None).semantics_confident


def test_transition_model_beats_unconditional_baseline_on_later_holdout() -> None:
    validation = validate_model()
    assert validation.test_size == 16
    assert validation.passed is True
    assert validation.transition_log_loss < validation.baseline_log_loss
    assert validation.transition_multiclass_brier < validation.baseline_multiclass_brier


def test_fed_shadow_is_market_blind_and_has_distribution() -> None:
    shadow = predict_shadow(QUESTION, RULE, _snapshot(date(2025, 8, 1)), _policy())
    assert shadow.available is True
    assert shadow.probability == shadow.distribution["HIKE_25"]
    assert round(sum(shadow.distribution.values()), 12) == 1.0
    assert shadow.diagnostics["validation"]["passed"] is True


def test_live_snapshot_requires_official_prior_action() -> None:
    shadow = predict_shadow(QUESTION.replace("2025", "2026"), RULE, _snapshot(date(2026, 8, 12)))
    assert shadow.available is False
    assert shadow.reason_code == "FOMC_PRIOR_POLICY_ACTION_UNAVAILABLE"


def test_live_policy_action_is_an_input_not_a_training_row() -> None:
    shadow = predict_shadow(QUESTION.replace("2025", "2026"), RULE, None, _policy(date(2026, 7, 29)))
    assert shadow.available is True
    assert shadow.diagnostics["prior_action"] == "UNCHANGED"
    assert shadow.diagnostics["sample_size"] == 40
    assert shadow.diagnostics["policy_decision"]["raw_source_url"] == "https://fed.example/statement"


def test_fed_semantics_routes_to_macro_policy() -> None:
    proposition = parse_market_proposition(QUESTION, RULE)
    route = route_archetype(proposition, QUESTION, RULE, "CENTRAL_BANKS")
    assert route.name == "MACRO_POLICY"
    assert route.capability_state == "SHADOW_VALIDATED"


def test_model_registry_storage_is_versioned(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "registry.db")
    dataset, model = registry_records()
    storage.save_forecast_dataset(dataset)
    storage.save_forecast_model(model)
    assert storage.schema_version() >= 32
    assert storage.connection.execute("SELECT COUNT(*) FROM forecast_datasets").fetchone()[0] == 1
    assert storage.connection.execute("SELECT active FROM forecast_models").fetchone()[0] == 1
    storage.close()


def test_engine_exposes_only_archetype_model_shadow_for_exact_fed_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.prediction.engine.fetch_macro_snapshot", lambda: _snapshot(date(2025, 8, 1)))
    monkeypatch.setattr("polymarketpulse.providers.fedboard.fetch_latest_policy_decision", lambda: _policy())
    storage = Storage(tmp_path / "fed-engine.db")
    result = compute_prediction(
        storage.connection, "fed-test", "polymarket", "fed-test",
        "CENTRAL_BANKS", 0.20, 100000, 90, 0, None, True,
        question=QUESTION, resolution_text=RULE,
    )
    assert result.forecast_archetype == "MACRO_POLICY"
    assert result.model_hypothesis_probability is not None
    assert result.numeric_model_reason_code is None
    assert any(item.name == "macro_policy" and item.available for item in result.submodel_estimates)
    assert not any(item.name == "macro" and item.available for item in result.submodel_estimates)
    storage.close()
