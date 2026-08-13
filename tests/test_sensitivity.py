from polymarketpulse.prediction.sensitivity import derive_sensitivity_audit
from polymarketpulse.prediction.types import SubmodelEstimate


def _model(name: str, probability: float, weight: float = 1.0) -> SubmodelEstimate:
    return SubmodelEstimate(name=name, estimated_yes_probability=probability, weight=weight, available=True, detail=name)


def test_exact_linear_counterfactuals_are_recomputed_without_the_removed_model() -> None:
    audit = derive_sensitivity_audit((_model("history", 0.2), _model("macro", 0.8), _model("news", 0.9)))
    assert audit.applies_to == "pre_news_linear_ensemble"
    assert audit.baseline_probability == 0.5
    history = next(item for item in audit.counterfactuals if item.removed_input == "history")
    macro = next(item for item in audit.counterfactuals if item.removed_input == "macro")
    assert (history.without_probability, history.delta) == (0.8, 0.3)
    assert (macro.without_probability, macro.delta) == (0.2, -0.3)
    assert audit.strongest_input == "history"
    assert audit.fragility == "MEASURED"


def test_news_and_market_price_never_receive_fake_counterfactual_numbers() -> None:
    audit = derive_sensitivity_audit((_model("history", 0.6), _model("news", 0.1)))
    values = {item.removed_input: item for item in audit.counterfactuals}
    assert values["news"].status == "NOT_APPLICABLE"
    assert values["news"].delta is None
    assert values["market_price"].status == "NOT_APPLICABLE"
    assert values["market_price"].without_probability is None


def test_single_input_is_reported_as_fragile_without_making_up_a_delta() -> None:
    audit = derive_sensitivity_audit((_model("history", 0.6),))
    history = next(item for item in audit.counterfactuals if item.removed_input == "history")
    assert audit.fragility == "SINGLE_INPUT"
    assert history.status == "UNAVAILABLE"
    assert history.delta is None
