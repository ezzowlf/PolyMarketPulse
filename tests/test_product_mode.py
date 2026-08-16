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


def test_structured_market_never_receives_a_fake_probability() -> None:
    prediction = SimpleNamespace(
        forecast_archetype="LEGISLATIVE_PROCESS",
        model_hypothesis_probability=None,
        numeric_model_reason_code="MODEL_NOT_VALIDATED",
        model_diagnostics={},
        structured_world_state=object(),
        next_event=None,
        scenario_tree=None,
        world_state=None,
    )
    product = product_mode_for_prediction(prediction)
    assert product["product_mode"] == "STRUCTURED_OUTLOOK"
    assert product["product_probability"] is None


def test_list_mode_is_storage_only_and_conservative() -> None:
    assert product_mode_for_market_record({"question": "Fed decision", "model_hypothesis_probability": 0.7, "has_champion_macro_model": 1}) == "VALIDATED_NUMERIC_FORECAST"
    assert product_mode_for_market_record({"question": "Fed decision", "model_hypothesis_probability": 0.7}) == "INSUFFICIENT_DATA"
    assert product_mode_for_market_record({"question": "Clarity Act", "has_research_run": 1}) == "STRUCTURED_OUTLOOK"
    assert product_mode_for_market_record({"question": "Anything"}) == "INSUFFICIENT_DATA"
