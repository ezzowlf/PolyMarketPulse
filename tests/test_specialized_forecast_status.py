from pathlib import Path

from polymarketpulse.prediction.engine import _forecast_status, compute_prediction
from polymarketpulse.prediction.types import SubmodelEstimate
from polymarketpulse.storage import Storage


def _estimate(name: str, *, available: bool = True) -> SubmodelEstimate:
    return SubmodelEstimate(
        name=name,
        estimated_yes_probability=0.62 if available else None,
        weight=1.0,
        available=available,
        detail="test",
    )


def test_production_specialized_model_can_carry_independent_forecast() -> None:
    status = _forecast_status(
        estimated_yes=0.62,
        independent_probability=0.62,
        submodel_estimates=[_estimate("macro")],
        confidence=70.0,
    )

    assert status == "INDEPENDENT_FORECAST"


def test_specialized_only_forecast_still_respects_low_data_gate() -> None:
    status = _forecast_status(
        estimated_yes=0.62,
        independent_probability=0.62,
        submodel_estimates=[_estimate("quant")],
        confidence=44.9,
    )

    assert status == "LOW_DATA"


def test_unavailable_specialized_model_does_not_create_forecast() -> None:
    status = _forecast_status(
        estimated_yes=0.62,
        independent_probability=0.62,
        submodel_estimates=[_estimate("macro", available=False)],
        confidence=80.0,
    )

    assert status == "NO_FORECAST"


def test_macro_only_estimate_flows_through_full_prediction(tmp_path: Path, monkeypatch) -> None:
    storage = Storage(tmp_path / "macro-only.db")
    fetch_calls = []

    def offline_fred():
        fetch_calls.append(True)

    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.fetch_macro_snapshot", offline_fred
    )
    kwargs = {
        "market_id": "fed-hold",
        "provider": "polymarket",
        "provider_market_id": "fed-hold",
        "category": "CENTRAL_BANKS",
        "market_yes_price": 0.55,
        "liquidity": 50_000,
        "data_quality_report_score": 90,
        "news_count": 0,
        "news_agreement": None,
        "resolution_rules_present": True,
        "question": "Will there be no change in Fed interest rates after the September 2026 meeting?",
        "resolution_text": "Resolves YES if the target range is unchanged after the meeting.",
    }

    result = compute_prediction(storage.connection, **kwargs)
    repeated = compute_prediction(storage.connection, **kwargs)

    macro = next(item for item in result.submodel_estimates if item.name == "macro")
    assert macro.available is True
    assert result.independent_probability == macro.estimated_yes_probability
    assert result.estimated_yes_probability is not None
    assert result.forecast_status in {"INDEPENDENT_FORECAST", "LOW_DATA"}
    assert repeated.independent_probability == result.independent_probability
    assert len(fetch_calls) == 1
