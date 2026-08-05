"""Percent-vs-fraction and INSUFFICIENT_DATA/direction=NONE consistency —
introduced after a live GPT-5-nano test returned `market_yes_percent=0.135`
(a fraction) instead of `13.5` (a percent), and separately returned
`direction=NO` for an `INSUFFICIENT_DATA` recommendation. Every OpenAI
interaction here is fully mocked — no real network call is possible."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pydantic
import pytest

from polymarketpulse.ai import service as ai_service
from polymarketpulse.ai.fallback import direction_for
from polymarketpulse.ai.schemas import ExplanationResult, ProbabilityExplanation
from polymarketpulse.ai.validation import ValidationError, validate_explanation
from polymarketpulse.config import Settings
from polymarketpulse.models import Market
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


def _valid_explanation_dict(prediction, direction: str | None = None) -> dict:
    result = ExplanationResult(
        direction=direction if direction is not None else direction_for(prediction.recommendation),
        recommendation=prediction.recommendation,
        headline="Test", summary="Test summary",
        probability_explanation=ProbabilityExplanation(
            market_yes_percent=round(prediction.market_yes_probability * 100, 1) if prediction.market_yes_probability is not None else None,
            estimated_yes_percent=round(prediction.estimated_yes_probability * 100, 1) if prediction.estimated_yes_probability is not None else None,
            estimated_no_percent=round(prediction.estimated_no_probability * 100, 1) if prediction.estimated_no_probability is not None else None,
            confidence_percent=round(prediction.confidence_score, 1),
            net_edge_percentage_points=round(prediction.net_yes_edge * 100, 1) if prediction.net_yes_edge is not None else None,
        ),
        supports_yes=[], supports_no=[], uncertainties=["test"], data_gaps=[],
        historical_context="Test", recommendation_explanation="Test",
        warning="Prognose, keine Gewissheit.",
    )
    return result.model_dump()


class RecordingClient:
    """Like ScriptedClient but also records every `user_prompt` it was
    called with, so the repair-attempt's prompt content can be inspected."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0
        self.prompts: list[str] = []

    def generate_structured(self, system_prompt, user_prompt, schema_model, schema_name):
        self.calls += 1
        self.prompts.append(user_prompt)
        if not self.script:
            raise AssertionError("RecordingClient called more times than scripted")
        return self.script.pop(0)()


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def ai_settings(tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-nano")
    monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "gpt-5-mini")
    base = Settings.load()
    return replace(
        base, database_path=tmp_path / "test.db", ai_enabled=True, openai_api_key="sk-fake-test-key",
        openai_model="gpt-5-nano", openai_fallback_model="gpt-5-mini", ai_cache_ttl_seconds=900,
    )


def _seed_market(storage: Storage, category="esports", yes_price=0.5) -> str:
    market = Market(
        provider="polymarket", provider_market_id="1", condition_id="", question="Will Team A win?",
        slug="team-a", category=category, liquidity=100000, volume_24h=20000, yes_price=yes_price,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = '1'"
    ).fetchone()[0]


def _insufficient_data_prediction(storage: Storage, market_id: str):
    """No resolved history seeded -> sample_size 0 -> INSUFFICIENT_DATA."""
    return compute_prediction(
        storage.connection, market_id, "polymarket", "1", "esports", 0.135, 100000, None, 0, None, False
    )


# --- 1/2: INSUFFICIENT_DATA must pair only with direction=NONE ------------


def test_insufficient_data_with_direction_none_is_accepted(storage: Storage) -> None:
    market_id = _seed_market(storage, yes_price=0.135)
    prediction = _insufficient_data_prediction(storage, market_id)
    assert prediction.recommendation == "INSUFFICIENT_DATA"
    explanation = ExplanationResult.model_validate(_valid_explanation_dict(prediction, direction="NONE"))
    validate_explanation(explanation, prediction, set())  # must not raise


def test_insufficient_data_with_direction_no_is_rejected(storage: Storage) -> None:
    market_id = _seed_market(storage, yes_price=0.135)
    prediction = _insufficient_data_prediction(storage, market_id)
    explanation = ExplanationResult.model_validate(_valid_explanation_dict(prediction, direction="NO"))
    with pytest.raises(ValidationError, match="INSUFFICIENT_DATA"):
        validate_explanation(explanation, prediction, set())


# --- 3/4: percent vs. fraction -----------------------------------------


def test_percent_value_13_5_is_accepted_by_schema() -> None:
    pe = ProbabilityExplanation(market_yes_percent=13.5, estimated_yes_percent=13.6, confidence_percent=54.5)
    assert pe.market_yes_percent == 13.5


def test_fraction_value_in_percent_field_within_range_but_wrong_vs_engine(storage: Storage) -> None:
    """0.135 is technically within the schema's 0-100 range, so it passes
    Pydantic — the cross-check against the engine's actual percent (13.5)
    is what must catch it, with a clear, specific error message."""
    market_id = _seed_market(storage, yes_price=0.135)
    prediction = _insufficient_data_prediction(storage, market_id)
    bad = _valid_explanation_dict(prediction, direction="NONE")
    bad["probability_explanation"]["market_yes_percent"] = 0.135
    explanation = ExplanationResult.model_validate(bad)
    with pytest.raises(ValidationError, match="market_yes_percent"):
        validate_explanation(explanation, prediction, set())


# --- 5/6: out-of-range percent/confidence rejected at the schema level ---


def test_probability_percent_above_100_rejected_by_schema() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProbabilityExplanation(market_yes_percent=135.0)


def test_probability_percent_below_0_rejected_by_schema() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProbabilityExplanation(market_yes_percent=-5.0)


def test_confidence_percent_above_100_rejected_by_schema() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProbabilityExplanation(confidence_percent=154.5)


def test_confidence_percent_below_0_rejected_by_schema() -> None:
    with pytest.raises(pydantic.ValidationError):
        ProbabilityExplanation(confidence_percent=-1.0)


# --- 7/8: rounding tolerance ---------------------------------------------


def test_small_rounding_deviation_is_accepted(storage: Storage) -> None:
    market_id = _seed_market(storage, yes_price=0.135)
    prediction = _insufficient_data_prediction(storage, market_id)
    bad = _valid_explanation_dict(prediction, direction="NONE")
    engine_pct = round(prediction.market_yes_probability * 100)
    bad["probability_explanation"]["market_yes_percent"] = engine_pct + 0.9  # within TOLERANCE_PP=1.0
    explanation = ExplanationResult.model_validate(bad)
    validate_explanation(explanation, prediction, set())  # must not raise


def test_excessive_deviation_from_engine_value_is_rejected(storage: Storage) -> None:
    market_id = _seed_market(storage, yes_price=0.135)
    prediction = _insufficient_data_prediction(storage, market_id)
    bad = _valid_explanation_dict(prediction, direction="NONE")
    engine_pct = round(prediction.market_yes_probability * 100)
    bad["probability_explanation"]["market_yes_percent"] = engine_pct + 5.0  # well beyond tolerance
    explanation = ExplanationResult.model_validate(bad)
    with pytest.raises(ValidationError, match="market_yes_percent"):
        validate_explanation(explanation, prediction, set())


def test_confidence_deviation_beyond_tolerance_is_rejected(storage: Storage) -> None:
    market_id = _seed_market(storage, yes_price=0.135)
    prediction = _insufficient_data_prediction(storage, market_id)
    bad = _valid_explanation_dict(prediction, direction="NONE")
    bad["probability_explanation"]["confidence_percent"] = round(prediction.confidence_score) + 10.0
    explanation = ExplanationResult.model_validate(bad)
    with pytest.raises(ValidationError, match="confidence_percent"):
        validate_explanation(explanation, prediction, set())


# --- 9/10: repair prompt names the concrete error, then succeeds --------


def test_repair_prompt_contains_concrete_violation(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage, yes_price=0.135)

    def wrong_direction():
        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.135, 100000, None, 0, None, False
        )
        return _valid_explanation_dict(prediction, direction="NO"), 300, 60

    def correct_direction():
        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.135, 100000, None, 0, None, False
        )
        return _valid_explanation_dict(prediction, direction="NONE"), 300, 60

    nano = RecordingClient(wrong_direction, correct_direction)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    assert nano.calls == 2
    repair_prompt = nano.prompts[1]
    assert "KORREKTUR" in repair_prompt
    assert "NONE" in repair_prompt or "INSUFFICIENT_DATA" in repair_prompt
    assert response.meta.used_fallback is False
    assert response.explanation.direction == "NONE"


def test_successful_response_after_repair(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage, yes_price=0.135)

    def bad_percent():
        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.135, 100000, None, 0, None, False
        )
        bad = _valid_explanation_dict(prediction, direction="NONE")
        bad["probability_explanation"]["market_yes_percent"] = 0.135  # the exact real-world bug
        return bad, 300, 60

    def good_percent():
        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.135, 100000, None, 0, None, False
        )
        return _valid_explanation_dict(prediction, direction="NONE"), 300, 60

    nano = RecordingClient(bad_percent, good_percent)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    assert response.meta.used_fallback is False
    assert response.explanation.probability_explanation.market_yes_percent == pytest.approx(13.5, abs=1.0)


# --- 11/12: engine values never change ------------------------------------


def test_probabilities_remain_unchanged_by_ai(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage, yes_price=0.135)

    def responder():
        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.135, 100000, None, 0, None, False
        )
        return _valid_explanation_dict(prediction, direction="NONE"), 300, 60

    nano = RecordingClient(responder)
    engine_only = ai_service.get_prediction(storage, market_id)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    assert response.prediction["market_yes_probability"] == engine_only.market_yes_probability
    assert response.prediction["estimated_yes_probability"] == engine_only.estimated_yes_probability


def test_confidence_remains_unchanged_by_ai(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage, yes_price=0.135)

    def responder():
        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.135, 100000, None, 0, None, False
        )
        return _valid_explanation_dict(prediction, direction="NONE"), 300, 60

    nano = RecordingClient(responder)
    engine_only = ai_service.get_prediction(storage, market_id)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    assert response.prediction["confidence_score"] == engine_only.confidence_score


# --- 13: fallback still works --------------------------------------------


def test_fallback_still_works_when_ai_disabled(storage: Storage) -> None:
    market_id = _seed_market(storage, yes_price=0.135)
    disabled = replace(Settings.load(), database_path=Path("x"), ai_enabled=False)
    response = ai_service.explain_recommendation(storage, disabled, market_id)
    assert response.meta.used_fallback is True
    assert response.explanation.direction in ("YES", "NO", "NONE")
    if response.prediction["recommendation"] == "INSUFFICIENT_DATA":
        assert response.explanation.direction == "NONE"
