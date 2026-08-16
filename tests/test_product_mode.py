from types import SimpleNamespace

from polymarketpulse.product_mode import product_mode_for_market_record, product_mode_for_prediction


def test_validated_fed_model_is_the_only_numeric_product_mode() -> None:
    prediction = SimpleNamespace(
        forecast_archetype="MACRO_POLICY",
        model_hypothesis_probability=0.7143,
        numeric_model_reason_code=None,
        model_diagnostics={"validation": {"passed": True}},
        structured_world_state=None,
        next_event=None,
        scenario_tree=None,
        world_state=None,
    )
    product = product_mode_for_prediction(prediction)
    assert product["product_mode"] == "VALIDATED_NUMERIC_FORECAST"
    assert product["product_probability"] == 0.7143
    assert product["model_lifecycle"] == "CHAMPION"


def _empty_structured_world_state() -> SimpleNamespace:
    return SimpleNamespace(
        current_state=None, confirmed_facts=(), completed_steps=(), open_steps=(),
        blockers=(), disputed_facts=(),
    )


def test_structured_market_never_receives_a_fake_probability() -> None:
    prediction = SimpleNamespace(
        forecast_archetype="LEGISLATIVE_PROCESS",
        model_hypothesis_probability=None,
        numeric_model_reason_code="MODEL_NOT_VALIDATED",
        model_diagnostics={},
        structured_world_state=SimpleNamespace(
            current_state="Zuletzt bestätigt: house_vote (LEGISLATION).",
            confirmed_facts=("Clarity Act officially passed the House.",),
            completed_steps=("introduced", "committee", "house_vote"),
            open_steps=("senate_vote", "presidential_action"),
            blockers=(), disputed_facts=(),
        ),
        next_event=SimpleNamespace(next_event_type="SENATE_VOTE"),
        scenario_tree=None,
        world_state=None,
        data_gaps=None,
    )
    product = product_mode_for_prediction(prediction)
    assert product["product_mode"] == "STRUCTURED_OUTLOOK"
    assert product["product_probability"] is None


def test_literal_unknown_current_state_string_is_not_real_content() -> None:
    """Regression: world_state.py's PathToResolution.current_state falls
    back to the literal string "UNKNOWN" when nothing real is known, which
    is a truthy non-empty string -- it must not itself trigger
    STRUCTURED_OUTLOOK for a market where nothing else is known either."""
    prediction = SimpleNamespace(
        forecast_archetype=None,
        model_hypothesis_probability=None,
        numeric_model_reason_code="NO_ARCHETYPE",
        model_diagnostics={},
        structured_world_state=SimpleNamespace(
            current_state="UNKNOWN", confirmed_facts=(), completed_steps=(), open_steps=(),
            blockers=(), disputed_facts=(),
        ),
        next_event=SimpleNamespace(next_event_type=None),
        scenario_tree=None,
        world_state=None,
        data_gaps=None,
    )
    product = product_mode_for_prediction(prediction)
    assert product["product_mode"] == "INSUFFICIENT_DATA"


def test_object_presence_alone_is_not_enough_for_structured_outlook() -> None:
    """Regression: structured_world_state/next_event/world_state are
    non-None dataclass instances on almost every successfully-computed
    prediction (engine.py constructs them unconditionally) -- a bare
    truthiness check on the object itself would make INSUFFICIENT_DATA
    unreachable in practice. Only real, non-empty content should count."""
    prediction = SimpleNamespace(
        forecast_archetype=None,
        model_hypothesis_probability=None,
        numeric_model_reason_code="NO_ARCHETYPE",
        model_diagnostics={},
        structured_world_state=_empty_structured_world_state(),
        next_event=SimpleNamespace(next_event_type=None),
        scenario_tree=None,
        world_state=SimpleNamespace(),  # present, but carries nothing real
        data_gaps=None,
    )
    product = product_mode_for_prediction(prediction)
    assert product["product_mode"] == "INSUFFICIENT_DATA"
    assert product["product_probability"] is None


def test_list_mode_is_storage_only_and_conservative() -> None:
    assert product_mode_for_market_record({"question": "Fed decision", "model_hypothesis_probability": 0.7, "has_champion_macro_model": 1}) == "VALIDATED_NUMERIC_FORECAST"
    assert product_mode_for_market_record({"question": "Fed decision", "model_hypothesis_probability": 0.7}) == "INSUFFICIENT_DATA"
    assert product_mode_for_market_record({"question": "Clarity Act", "has_research_run": 1}) == "STRUCTURED_OUTLOOK"
    assert product_mode_for_market_record({"question": "Anything"}) == "INSUFFICIENT_DATA"
