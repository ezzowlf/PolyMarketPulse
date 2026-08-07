from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.ai import service as ai_service
from polymarketpulse.ai.client import AITimeoutError
from polymarketpulse.ai.schemas import ExplanationFactor, ExplanationResult, ProbabilityExplanation
from polymarketpulse.config import Settings
from polymarketpulse.models import Market
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


def _valid_explanation_for(prediction) -> ExplanationResult:
    from polymarketpulse.ai.fallback import direction_for

    return ExplanationResult(
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
        supports_yes=[],
        supports_no=[],
        uncertainties=["test"],
        data_gaps=[],
        historical_context="Test",
        recommendation_explanation="Test",
        warning="Prognose, keine Gewissheit.",
    )


class FakeNanoClient:
    def __init__(self, responder=None):
        self.calls = 0
        self.responder = responder

    def generate_structured(self, system_prompt, user_prompt, schema_model, schema_name):
        self.calls += 1
        if self.responder:
            return self.responder(self.calls)
        raise AssertionError("no responder configured")


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def ai_settings(tmp_path: Path, monkeypatch) -> Settings:
    # Force these explicitly rather than trusting whatever a real local
    # .env happens to have — a previous session's live-smoke-test may have
    # left OPENAI_MODEL set to something else on disk.
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-nano")
    monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "gpt-5-mini")
    base = Settings.load()
    return replace(
        base,
        database_path=tmp_path / "test.db",
        ai_enabled=True,
        openai_api_key="sk-fake-test-key",
        openai_model="gpt-5-nano",
        openai_fallback_model="gpt-5-mini",
        ai_cache_ttl_seconds=900,
    )


# A real, classifiable question (not a placeholder like "Will Team A win?",
# which the Phase C classifier can't confidently place into a category —
# see classification.py's SPORT_OTHER esports keywords). Phase E wires
# history.py's Phase D similarity-weighted comparable-case scorer
# (find_comparable_cases/compute_weighted_baseline) into engine.py's real
# call path, which means these fixtures now need question text/metadata
# that actually round-trips through classify_market()/parse_market_proposition()
# the same way a real seeded market would — a bare "x" question or an
# unparseable one previously scored 0.0 similarity against everything
# (weight <= 0 is excluded from the weighted baseline), silently collapsing
# comparable_sample_size to 0 and flipping NO_BET into INSUFFICIENT_DATA.
_ESPORTS_QUESTION_TEMPLATE = "Will {name} win the League of Legends championship?"


def _seed_market(storage: Storage, category="esports", yes_price=0.5) -> str:
    market = Market(
        provider="polymarket",
        provider_market_id="1",
        condition_id="",
        question=_ESPORTS_QUESTION_TEMPLATE.format(name="Team A"),
        slug="team-a",
        category=category,
        liquidity=100000,
        volume_24h=20000,
        yes_price=yes_price,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = '1'"
    ).fetchone()[0]


def _seed_resolved_history(storage: Storage, n_yes: int, n_no: int, category="esports") -> None:
    # Classify each seeded historical question through the real Phase A/C
    # pipeline (not hand-picked labels) so the similarity scorer in
    # history.py sees genuine classified_category/event_type/proposition
    # data — exactly what a real backfilled market row looks like.
    import json

    from polymarketpulse.prediction.classification import classify_market
    from polymarketpulse.prediction.semantics import parse_market_proposition

    def _insert(pmid: str, question: str, outcome: str) -> None:
        proposition = parse_market_proposition(question, None)
        classification = classify_market(question, None, proposition)
        storage.connection.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
            "first_seen_at, last_seen_at, resolution_status, category, classified_category, "
            "event_type, entities_json, proposition_json) "
            "VALUES (?, 'polymarket', ?, ?, 'x', 'https://x', ?, ?, 'resolved', ?, ?, ?, ?, ?)",
            (
                pmid, pmid, question, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(),
                category, classification.category, classification.event_type,
                json.dumps([]),
                json.dumps({"proposition_status": proposition.proposition_status, "location": proposition.location}),
            ),
        )
        storage.connection.execute(
            "INSERT INTO market_resolutions (provider, provider_market_id, resolved_at, winning_outcome, status, detected_at) "
            "VALUES ('polymarket', ?, ?, ?, 'resolved', ?)",
            (pmid, datetime.now(UTC).isoformat(), outcome, datetime.now(UTC).isoformat()),
        )

    for i in range(n_yes):
        _insert(f"h-yes-{i}", _ESPORTS_QUESTION_TEMPLATE.format(name=f"Team Yes{i}"), "Yes")
    for i in range(n_no):
        _insert(f"h-no-{i}", _ESPORTS_QUESTION_TEMPLATE.format(name=f"Team No{i}"), "No")
    storage.connection.commit()


def test_uses_gpt5_nano_by_default(ai_settings: Settings) -> None:
    assert ai_settings.openai_model == "gpt-5-nano"
    assert ai_settings.openai_fallback_model == "gpt-5-mini"


def test_fallback_used_when_ai_disabled(storage: Storage) -> None:
    market_id = _seed_market(storage)
    disabled = replace(Settings.load(), database_path=Path("x"), ai_enabled=False)
    response = ai_service.explain_recommendation(storage, disabled, market_id)
    assert response.meta.used_fallback is True
    assert "deaktiviert" in response.meta.fallback_reason.lower()
    assert response.explanation.recommendation == response.prediction["recommendation"]


def test_fallback_never_leaves_empty_explanation(storage: Storage) -> None:
    market_id = _seed_market(storage)
    disabled = replace(Settings.load(), database_path=Path("x"), ai_enabled=False)
    response = ai_service.explain_recommendation(storage, disabled, market_id)
    assert response.explanation.summary
    assert response.explanation.recommendation_explanation


def test_market_not_found_raises_context_error(storage: Storage, ai_settings: Settings) -> None:
    from polymarketpulse.ai.client import AIContextError

    with pytest.raises(AIContextError):
        ai_service.explain_recommendation(storage, ai_settings, "does-not-exist")


def test_valid_nano_response_is_used_and_not_mini(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)

    def responder(call_n):
        # Need the prediction first to build a matching valid response —
        # recompute it the same way the service does.
        from polymarketpulse.prediction import compute_prediction

        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.5, 100000, None, 0, None, False,
            question=_ESPORTS_QUESTION_TEMPLATE.format(name="Team A"),
        )
        explanation = _valid_explanation_for(prediction)
        return explanation.model_dump(), 500, 100

    nano = FakeNanoClient(responder)
    mini = FakeNanoClient(responder)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)
    assert response.meta.used_fallback is False
    assert nano.calls == 1
    assert mini.calls == 0
    assert response.meta.model == "gpt-5-nano"


def test_second_call_hits_cache_no_network(storage: Storage, ai_settings: Settings) -> None:
    market_id = _seed_market(storage)

    def responder(call_n):
        from polymarketpulse.prediction import compute_prediction

        prediction = compute_prediction(
            storage.connection, market_id, "polymarket", "1", "esports", 0.5, 100000, None, 0, None, False,
            question=_ESPORTS_QUESTION_TEMPLATE.format(name="Team A"),
        )
        return _valid_explanation_for(prediction).model_dump(), 500, 100

    nano = FakeNanoClient(responder)
    ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)
    response2 = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)
    assert response2.meta.cached is True
    assert nano.calls == 1  # second lookup never called the fake network again


def test_invalid_json_twice_falls_back_to_mini(storage: Storage, ai_settings: Settings) -> None:
    def bad_responder(call_n):
        raise AITimeoutError("simulated failure")

    def good_responder(call_n):
        from polymarketpulse.prediction import compute_prediction

        market_id_local = "polymarket:1"
        prediction = compute_prediction(
            storage.connection, market_id_local, "polymarket", "1", "esports", 0.5, 100000, None, 0, None, False,
            question=_ESPORTS_QUESTION_TEMPLATE.format(name="Team A"),
        )
        return _valid_explanation_for(prediction).model_dump(), 400, 90

    market_id = _seed_market(storage)
    nano = FakeNanoClient(bad_responder)
    mini = FakeNanoClient(good_responder)
    # Escalation to gpt-5-mini is opt-in (OPENAI_ESCALATION_ENABLED=false by
    # default) — explicitly enable it for this test of that specific path.
    escalation_settings = replace(ai_settings, openai_escalation_enabled=True)
    response = ai_service.explain_recommendation(storage, escalation_settings, market_id, nano_client=nano, mini_client=mini)
    assert nano.calls == 2  # one retry on the same model before falling to mini
    assert mini.calls == 1
    assert response.meta.model == "gpt-5-mini"
    assert response.meta.used_fallback is False


def test_mini_escalation_disabled_by_default(storage: Storage, ai_settings: Settings) -> None:
    def bad_responder(call_n):
        raise AITimeoutError("simulated failure")

    market_id = _seed_market(storage)
    nano = FakeNanoClient(bad_responder)
    mini = FakeNanoClient(lambda call_n: (_ for _ in ()).throw(AssertionError("mini must not be called by default")))
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)
    assert ai_settings.openai_escalation_enabled is False
    assert mini.calls == 0
    assert response.meta.used_fallback is True


def test_all_models_failing_uses_rule_based_fallback(storage: Storage, ai_settings: Settings) -> None:
    def always_fails(call_n):
        raise AITimeoutError("simulated failure")

    market_id = _seed_market(storage)
    nano = FakeNanoClient(always_fails)
    mini = FakeNanoClient(always_fails)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)
    assert response.meta.used_fallback is True
    assert response.explanation.summary


def test_mismatched_recommendation_is_rejected(storage: Storage, ai_settings: Settings) -> None:
    def wrong_recommendation(call_n):
        from polymarketpulse.prediction import compute_prediction

        prediction = compute_prediction(
            storage.connection, "polymarket:1", "polymarket", "1", "esports", 0.5, 100000, None, 0, None, False,
            question=_ESPORTS_QUESTION_TEMPLATE.format(name="Team A"),
        )
        bad = _valid_explanation_for(prediction).model_dump()
        bad["recommendation"] = "STRONG_YES" if prediction.recommendation != "STRONG_YES" else "STRONG_NO"
        bad["direction"] = "YES"
        return bad, 400, 90

    market_id = _seed_market(storage)
    nano = FakeNanoClient(wrong_recommendation)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=nano)
    # Both attempts (nano retry + nano-as-mini) return mismatched data -> falls back.
    assert response.meta.used_fallback is True
    assert response.explanation.recommendation == response.prediction["recommendation"]


def test_invented_source_id_is_rejected(storage: Storage, ai_settings: Settings) -> None:
    def invented_source(call_n):
        from polymarketpulse.prediction import compute_prediction

        prediction = compute_prediction(
            storage.connection, "polymarket:1", "polymarket", "1", "esports", 0.5, 100000, None, 0, None, False,
            question=_ESPORTS_QUESTION_TEMPLATE.format(name="Team A"),
        )
        explanation = _valid_explanation_for(prediction)
        explanation = explanation.model_copy(
            update={
                "supports_yes": [ExplanationFactor(factor="made up", impact="high", source_ids=["totally_fake_id"])]
            }
        )
        return explanation.model_dump(), 400, 90

    market_id = _seed_market(storage)
    nano = FakeNanoClient(invented_source)
    response = ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=nano)
    assert response.meta.used_fallback is True


def test_cost_estimate_stays_under_one_cent_for_typical_analysis() -> None:
    from polymarketpulse.ai.cost import estimate_cost

    estimate = estimate_cost("gpt-5-nano", 3000, 800)
    assert estimate.estimated_cost_usd < 0.01


def test_over_budget_request_falls_back_without_calling_api(storage: Storage, ai_settings: Settings) -> None:
    tiny_budget = replace(ai_settings, openai_max_cost_per_analysis_usd=0.0000001)
    market_id = _seed_market(storage)

    def should_never_be_called(call_n):
        raise AssertionError("API must not be called when over budget")

    nano = FakeNanoClient(should_never_be_called)
    response = ai_service.explain_recommendation(storage, tiny_budget, market_id, nano_client=nano)
    assert response.meta.used_fallback is True
    assert nano.calls == 0
    assert "kosten" in response.meta.fallback_reason.lower()


def test_daily_budget_exhausted_falls_back(storage: Storage, ai_settings: Settings) -> None:
    tiny_daily = replace(ai_settings, openai_daily_budget_usd=0.0)
    market_id = _seed_market(storage)
    nano = FakeNanoClient(lambda n: (_ for _ in ()).throw(AssertionError("must not call API")))
    response = ai_service.explain_recommendation(storage, tiny_daily, market_id, nano_client=nano)
    assert response.meta.used_fallback is True


def test_actual_token_usage_is_persisted(storage: Storage, ai_settings: Settings) -> None:
    def responder(call_n):
        from polymarketpulse.prediction import compute_prediction

        prediction = compute_prediction(
            storage.connection, "polymarket:1", "polymarket", "1", "esports", 0.5, 100000, None, 0, None, False,
            question=_ESPORTS_QUESTION_TEMPLATE.format(name="Team A"),
        )
        return _valid_explanation_for(prediction).model_dump(), 3210, 777

    market_id = _seed_market(storage)
    nano = FakeNanoClient(responder)
    ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano)

    row = storage.connection.execute(
        "SELECT input_tokens, output_tokens, actual_cost_usd, model FROM ai_analysis_runs "
        "WHERE analysis_type = 'explain_recommendation' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == 3210
    assert row[1] == 777
    assert row[2] is not None and row[2] < 0.01
    assert row[3] == "gpt-5-nano"


def test_data_changing_invalidates_cache(storage: Storage, ai_settings: Settings) -> None:
    def responder(call_n):
        from polymarketpulse.prediction import compute_prediction

        # Read the *current* price from the DB rather than hardcoding it, so
        # the fake response always matches what the engine actually computed
        # for the snapshot in effect at call time.
        current_price = storage.connection.execute(
            "SELECT yes_price FROM market_snapshots WHERE market_id = ? ORDER BY captured_at DESC LIMIT 1",
            (market_id,),
        ).fetchone()[0]
        # Read the *current* question from the DB too (not a hardcoded
        # constant) — the confidence composite (K1) now genuinely depends on
        # the parsed proposition, so the fake response must match whatever
        # question was actually live in the DB for this call, exactly like
        # it already does for current_price above.
        current_question = storage.connection.execute(
            "SELECT question FROM markets WHERE market_id = ?", (market_id,),
        ).fetchone()[0]
        prediction = compute_prediction(
            storage.connection, "polymarket:1", "polymarket", "1", "esports", current_price, 100000, None, 0, None, False,
            question=current_question or "",
        )
        return _valid_explanation_for(prediction).model_dump(), 400, 90

    market_id = _seed_market(storage)
    nano = FakeNanoClient(responder)
    # Explicitly pass a mock mini_client too: if this test's assumptions were
    # ever wrong and validation failed twice, the service would otherwise
    # escalate to a *real*, unmocked OpenAIStructuredClient — never allow
    # that path to be reachable in an automated test.
    mini = FakeNanoClient(responder)
    ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)

    # New scan changes the price -> new snapshot -> data_snapshot_version changes.
    changed = Market(
        provider="polymarket", provider_market_id="1", condition_id="", question="Will Team A win?",
        slug="team-a", category="esports", liquidity=100000, volume_24h=20000, yes_price=0.9,
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(changed, generate_signals(changed))])

    ai_service.explain_recommendation(storage, ai_settings, market_id, nano_client=nano, mini_client=mini)
    assert nano.calls == 2  # cache miss on the second, genuinely different call
    assert mini.calls == 0


def test_insufficient_data_recommendation_explained_correctly(storage: Storage) -> None:
    market_id = _seed_market(storage)
    disabled = replace(Settings.load(), database_path=Path("x"), ai_enabled=False)
    response = ai_service.explain_recommendation(storage, disabled, market_id)
    assert response.prediction["recommendation"] == "INSUFFICIENT_DATA"
    assert response.explanation.direction == "NONE"


def test_no_bet_recommendation_explained_correctly(storage: Storage) -> None:
    _seed_resolved_history(storage, n_yes=5, n_no=5)
    market_id = _seed_market(storage, yes_price=0.5)
    disabled = replace(Settings.load(), database_path=Path("x"), ai_enabled=False)
    response = ai_service.explain_recommendation(storage, disabled, market_id)
    assert response.prediction["recommendation"] == "NO_BET"
    assert response.explanation.direction == "NONE"


def test_cached_fallback_response_is_still_reported_as_fallback(storage: Storage) -> None:
    market_id = _seed_market(storage)
    disabled = replace(Settings.load(), database_path=Path("x"), ai_enabled=False)
    first = ai_service.explain_recommendation(storage, disabled, market_id)
    assert first.meta.used_fallback is True

    second = ai_service.explain_recommendation(storage, disabled, market_id)
    assert second.meta.cached is True
    assert second.meta.used_fallback is True
