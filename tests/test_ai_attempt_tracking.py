"""Attempt-level usage/cost/error tracking — introduced after a live
GPT-5-nano smoke test revealed that every failed call attempt (timeout,
invalid JSON, schema mismatch, inconsistent numbers, budget block) was
persisted identically (input_tokens=None, actual_cost_usd=0.0), making it
impossible to tell afterwards whether a real, billable OpenAI call had
happened. Every OpenAI interaction here is fully mocked — no real network
call is possible from this file."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.ai import service as ai_service
from polymarketpulse.ai.client import (
    AIInvalidJSONError,
    AINetworkError,
    AITimeoutError,
)
from polymarketpulse.ai.fallback import direction_for
from polymarketpulse.ai.schemas import ExplanationResult, ProbabilityExplanation
from polymarketpulse.config import Settings
from polymarketpulse.models import Market
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


def _valid_explanation_dict(prediction) -> dict:
    result = ExplanationResult(
        direction=direction_for(prediction.recommendation),
        recommendation=prediction.recommendation,
        headline="Test",
        summary="Test summary",
        probability_explanation=ProbabilityExplanation(
            market_yes_percent=round(prediction.market_yes_probability * 100) if prediction.market_yes_probability is not None else None,
            estimated_yes_percent=round(prediction.estimated_yes_probability * 100) if prediction.estimated_yes_probability is not None else None,
            estimated_no_percent=round(prediction.estimated_no_probability * 100) if prediction.estimated_no_probability is not None else None,
            confidence_percent=round(prediction.confidence_score),
            net_edge_percentage_points=round(prediction.net_yes_edge * 100) if prediction.net_yes_edge is not None else None,
        ),
        supports_yes=[], supports_no=[], uncertainties=["test"], data_gaps=[],
        historical_context="Test", recommendation_explanation="Test",
        warning="Prognose, keine Gewissheit.",
    )
    return result.model_dump()


class ScriptedClient:
    """Each `script` entry is a zero-arg callable: return `(dict, in_tok,
    out_tok)` for a successful API response, or raise for a failure —
    exactly mirroring what OpenAIStructuredClient.generate_structured()
    itself returns/raises. No network access, ever."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def generate_structured(self, system_prompt, user_prompt, schema_model, schema_name):
        self.calls += 1
        if not self.script:
            raise AssertionError("ScriptedClient called more times than scripted")
        action = self.script.pop(0)
        return action()


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


def _prediction_for(storage: Storage, market_id: str):
    return compute_prediction(
        storage.connection, market_id, "polymarket", "1", "esports", 0.5, 100000, None, 0, None, False
    )


def _attempts_for(storage: Storage, analysis_id: int) -> list[dict]:
    return storage.list_ai_model_attempts(analysis_id)


def test_successful_first_attempt(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    prediction = _prediction_for(storage, market_id)
    nano = ScriptedClient(lambda: (_valid_explanation_dict(prediction), 500, 100))

    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    assert response.meta.used_fallback is False
    assert response.meta.input_tokens == 500
    assert response.meta.output_tokens == 100
    assert nano.calls == 1

    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "success"
    assert attempts[0]["input_tokens"] == 500
    assert attempts[0]["output_tokens"] == 100
    assert attempts[0]["actual_model"] == "gpt-5-nano"


def test_first_attempt_valid_usage_invalid_json(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)

    def raise_invalid_json():
        raise AIInvalidJSONError("Response was not valid JSON", 420, 80)

    nano = ScriptedClient(raise_invalid_json, raise_invalid_json)  # main + repair both fail the same way
    mini_ok = ScriptedClient(lambda: (_valid_explanation_dict(_prediction_for(storage, market_id)), 600, 120))

    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini_ok)

    attempts = _attempts_for(storage, response.meta.analysis_id)
    first = attempts[0]
    assert first["status"] == "invalid_json"
    assert first["input_tokens"] == 420
    assert first["output_tokens"] == 80
    assert first["actual_cost_usd"] is not None and first["actual_cost_usd"] > 0
    assert first["actual_model"] == "gpt-5-nano"


def test_schema_error_with_valid_usage(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)

    def bad_schema():
        # Missing required fields -> ExplanationResult.model_validate fails
        # with a pydantic ValidationError, even though the client itself
        # already returned parsed JSON + real usage.
        return ({"direction": "YES"}, 300, 50)

    nano = ScriptedClient(bad_schema, bad_schema)
    mini = ScriptedClient(bad_schema)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert attempts[0]["status"] == "schema_validation_failed"
    assert attempts[0]["input_tokens"] == 300
    assert attempts[0]["output_tokens"] == 50


def test_inconsistency_with_valid_usage(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    prediction = _prediction_for(storage, market_id)

    def wrong_recommendation():
        bad = _valid_explanation_dict(prediction)
        bad["recommendation"] = "STRONG_YES" if prediction.recommendation != "STRONG_YES" else "STRONG_NO"
        bad["direction"] = "YES"
        return bad, 310, 60

    nano = ScriptedClient(wrong_recommendation, wrong_recommendation)
    mini = ScriptedClient(wrong_recommendation)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert attempts[0]["status"] == "inconsistent_with_engine"
    assert attempts[0]["input_tokens"] == 310


def test_successful_repair_attempt(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    prediction = _prediction_for(storage, market_id)

    nano = ScriptedClient(
        lambda: (_ for _ in ()).throw(AITimeoutError("timeout")),
        lambda: (_valid_explanation_dict(prediction), 450, 90),
    )
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    assert response.meta.used_fallback is False
    assert nano.calls == 2
    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert len(attempts) == 2
    assert attempts[0]["status"] == "timeout"
    assert attempts[0]["is_repair"] == 0
    assert attempts[1]["status"] == "success"
    assert attempts[1]["is_repair"] == 1


def test_failed_repair_attempt(storage: Storage, ai_settings: Settings) -> None:
    # Escalation disabled by default: only the nano main + repair attempts
    # happen, no third (mini) call.
    market_id = _seed_market(storage)

    def always_invalid():
        raise AIInvalidJSONError("bad json", 200, 40)

    nano = ScriptedClient(always_invalid, always_invalid)
    mini = ScriptedClient(lambda: (_ for _ in ()).throw(AssertionError("mini must not be called by default")))
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    assert response.meta.used_fallback is True
    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert len(attempts) == 2  # nano main, nano repair — no mini escalation by default
    assert attempts[0]["is_repair"] == 0
    assert attempts[1]["is_repair"] == 1
    assert mini.calls == 0
    assert response.meta.fallback_reason is not None


def test_failed_repair_then_escalation_when_enabled(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)

    def always_invalid():
        raise AIInvalidJSONError("bad json", 200, 40)

    nano = ScriptedClient(always_invalid, always_invalid)
    mini = ScriptedClient(always_invalid)
    escalation_settings = replace(ai_settings, openai_escalation_enabled=True)
    response = ai_service.explain_recommendation(storage, escalation_settings, market_id, nano_client=nano, mini_client=mini)

    assert response.meta.used_fallback is True
    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert len(attempts) == 3  # nano main, nano repair, mini escalation
    assert attempts[2]["actual_model"] == "gpt-5-mini"
    assert mini.calls == 1


def test_costs_of_both_attempts_are_summed(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    prediction = _prediction_for(storage, market_id)

    nano = ScriptedClient(
        lambda: (_ for _ in ()).throw(AIInvalidJSONError("bad json", 1000, 200)),
        lambda: (_valid_explanation_dict(prediction), 1000, 200),
    )
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    attempts = _attempts_for(storage, response.meta.analysis_id)
    summed_cost = sum(a["actual_cost_usd"] for a in attempts)
    assert response.meta.actual_cost_usd == pytest.approx(summed_cost, abs=1e-9)
    assert response.meta.input_tokens == 2000  # both attempts' tokens summed
    assert response.meta.output_tokens == 400


def test_second_attempt_skipped_for_budget(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    # First attempt's real cost plus the repair's own pre-flight estimate
    # together exceed this cap, but the first attempt's pre-flight estimate
    # alone (using the same small max_output_tokens) fits — so attempt 1 is
    # sent, and only the repair is blocked. Recalibrated for Block F Part 2's
    # larger (real Block A-E structured data) explanation payload — attempt
    # 1's pre-flight estimate alone is now ~0.0001 USD (was ~0.00006 USD
    # before that payload grew), so the cap must sit above that but still
    # well below attempt1 + repair combined.
    tight_budget = replace(ai_settings, openai_max_cost_per_analysis_usd=0.00015, openai_max_output_tokens=100)

    def expensive_failure():
        raise AIInvalidJSONError("bad json", 500, 100)

    nano = ScriptedClient(expensive_failure)
    response = ai_service.explain_recommendation(storage, tight_budget, market_id, nano_client=nano)

    assert nano.calls == 1  # repair never sent
    attempts = _attempts_for(storage, response.meta.analysis_id)
    # attempt 1: real, sent; attempt 2 (nano repair) blocked pre-flight by
    # the tight budget. No mini escalation attempt is recorded at all,
    # since escalation is disabled by default.
    assert len(attempts) == 2
    assert attempts[0]["actual_model"] == "gpt-5-nano"
    assert attempts[1]["actual_model"] is None
    assert attempts[1]["status"] in ("blocked_cost_limit", "blocked_daily_budget")


def test_escalation_blocked_by_remaining_budget(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)

    def cheap_failure():
        raise AIInvalidJSONError("bad json", 100, 20)

    # Budget covers two small nano attempts but not a third, pricier mini
    # attempt (mini costs 5x nano's rate).
    tight_budget = replace(
        ai_settings, openai_escalation_enabled=True,
        openai_max_cost_per_analysis_usd=0.00006, openai_max_output_tokens=50,
    )
    nano = ScriptedClient(cheap_failure, cheap_failure)
    mini = ScriptedClient(lambda: (_ for _ in ()).throw(AssertionError("mini must not be called when budget-blocked")))
    response = ai_service.explain_recommendation(storage, tight_budget, market_id, nano_client=nano, mini_client=mini)

    assert mini.calls == 0
    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert attempts[-1]["actual_model"] is None
    assert attempts[-1]["status"] in ("blocked_cost_limit", "blocked_daily_budget")


def test_timeout_without_usage(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    nano = ScriptedClient(
        lambda: (_ for _ in ()).throw(AITimeoutError("timed out")),
        lambda: (_ for _ in ()).throw(AITimeoutError("timed out")),
    )
    mini = ScriptedClient(lambda: (_ for _ in ()).throw(AITimeoutError("timed out")))
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert attempts[0]["status"] == "timeout"
    assert attempts[0]["input_tokens"] is None
    assert attempts[0]["output_tokens"] is None
    assert attempts[0]["actual_cost_usd"] is None  # unknown, never 0.0


def test_api_error_without_usage(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    nano = ScriptedClient(
        lambda: (_ for _ in ()).throw(AINetworkError("could not reach OpenAI")),
        lambda: (_ for _ in ()).throw(AINetworkError("could not reach OpenAI")),
    )
    mini = ScriptedClient(lambda: (_ for _ in ()).throw(AINetworkError("could not reach OpenAI")))
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    attempts = _attempts_for(storage, response.meta.analysis_id)
    assert attempts[0]["status"] == "network_error"
    assert attempts[0]["input_tokens"] is None
    assert attempts[0]["actual_cost_usd"] is None


def test_fallback_response_carries_real_usage_when_available(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)

    def invalid_with_usage():
        raise AIInvalidJSONError("bad json", 800, 150)

    nano = ScriptedClient(invalid_with_usage, invalid_with_usage)
    mini = ScriptedClient(invalid_with_usage)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    assert response.meta.used_fallback is True
    # This is the exact gap the smoke test found: a fallback response must
    # not silently discard real usage that a rejected call actually incurred.
    assert response.meta.input_tokens is not None
    assert response.meta.input_tokens > 0
    assert response.meta.actual_cost_usd is not None
    assert response.meta.actual_cost_usd > 0


def test_zero_cost_is_not_confused_with_unknown_cost(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    disabled = replace(ai_settings, ai_enabled=False)
    response = ai_service.explain_recommendation(storage, disabled, market_id)

    # AI disabled: no attempt was ever made, so cost is genuinely unknown —
    # never displayed or stored as a misleading 0.0.
    assert response.meta.actual_cost_usd is None
    assert response.meta.input_tokens is None


def test_cache_hit_starts_no_new_attempt(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    prediction = _prediction_for(storage, market_id)
    nano = ScriptedClient(lambda: (_valid_explanation_dict(prediction), 500, 100))

    first = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)
    assert nano.calls == 1

    second = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)
    assert second.meta.cached is True
    assert nano.calls == 1  # no new attempt, no new network call

    attempts_after_cache_hit = storage.connection.execute("SELECT COUNT(*) FROM ai_model_attempts").fetchone()[0]
    assert attempts_after_cache_hit == 1  # only the original run's attempt, nothing added on cache hit
    assert first.meta.analysis_id == second.meta.analysis_id


def test_error_details_never_contain_api_key(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)
    secret = "sk-super-secret-key-do-not-leak-1234567890"

    def leaky_error():
        raise AIInvalidJSONError(f"upstream said: {secret}", 100, 20)

    nano = ScriptedClient(leaky_error, leaky_error)
    mini = ScriptedClient(leaky_error)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    attempts = _attempts_for(storage, response.meta.analysis_id)
    # error_detail is truncated to 200 chars by design; the secret used
    # here is short enough that this test would still catch a real leak.
    for a in attempts:
        if a["error_detail"]:
            assert ai_settings.openai_api_key not in a["error_detail"]
