"""Block F verification.

Part 1: genuinely-derived scenarios (prediction/scenarios.py) — proves a
market with a real Block C ResolutionPath gets a rich, step-derived
scenario pair, a simple binary market gets a minimal honest pair from
resolution_semantics, and a market with neither gets an honestly empty
scenarios tuple (no fabricated richness).

Part 2: the GPT-5-nano presentation layer. Proves (a) the explanation input
payload now carries the real Block A-E structured fields (published_
forecast_probability, decision_state, change_triggers, ...) instead of the
old stale/missing ones, and (b) a MOCKED model response that tries to
smuggle a fabricated change_triggers entry is unconditionally overwritten
by the server with the real, already-computed prediction.change_triggers —
never trusted from the model. All OpenAI interaction here is mocked; no
network call is possible from this file (per this project's established
cost-control discipline).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from polymarketpulse.ai import service as ai_service
from polymarketpulse.ai.fallback import direction_for
from polymarketpulse.ai.schemas import ExplanationResult, ProbabilityExplanation
from polymarketpulse.config import Settings
from polymarketpulse.models import Market
from polymarketpulse.prediction.evidence import EvidenceFactor
from polymarketpulse.prediction.resolution_semantics import ResolutionSemantics
from polymarketpulse.prediction.scenarios import build_scenarios
from polymarketpulse.prediction.world_state import ResolutionPath, ResolutionStep
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage

# --- Part 1: scenarios ------------------------------------------------------


def _evidence_factor(title: str, matched: str) -> EvidenceFactor:
    return EvidenceFactor(
        news_event_id=1, title=title, source="reuters.com", source_domain="reuters.com",
        url="https://reuters.com/x", published_at=None, reliability=0.9, tone=0.0,
        matched_condition=matched, recency_weight=0.9, link_confidence=0.9,
    )


def test_resolution_path_market_gets_rich_derived_scenarios():
    path = ResolutionPath(
        applies=True,
        steps=(
            ResolutionStep(name="introduced", status="done"),
            ResolutionStep(name="committee", status="done"),
            ResolutionStep(name="house_vote", status="unknown"),
            ResolutionStep(name="senate_vote", status="blocked"),
            ResolutionStep(name="presidential_action", status="unknown"),
        ),
    )
    yes_ev = (_evidence_factor("Committee approves bill", "yes"),)
    no_ev = (_evidence_factor("Senate vote delayed indefinitely", "no"),)
    result = build_scenarios(
        estimated_yes_probability=0.4, submodel_estimates=[], news_evidence=[],
        comparable_sample_size=0, recommendation="NO_BET",
        resolution_path=path, resolution_semantics=None,
        evidence_for_yes=yes_ev, evidence_for_no=no_ev,
        change_triggers=("Forecast würde sich ändern bei: die Abstimmung im Senat.",),
    )
    assert len(result.scenarios) == 2
    yes_s = next(s for s in result.scenarios if s.outcome == "YES")
    no_s = next(s for s in result.scenarios if s.outcome == "NO")
    # Real step names appear, in order, as necessary events for YES (only
    # the not-yet-done ones).
    assert "Abstimmung im Repräsentantenhaus" in yes_s.necessary_events
    assert "Abstimmung im Senat" in yes_s.necessary_events
    assert "Einbringung des Gesetzentwurfs" not in yes_s.necessary_events  # already done
    assert yes_s.supporting_claims == ("Committee approves bill",)
    assert yes_s.contradicting_claims == ("Senate vote delayed indefinitely",)
    assert "Abstimmung im Senat" in no_s.description or "Senat" in no_s.description
    # No fabricated probability on either scenario.
    assert yes_s.probability is None
    assert no_s.probability is None
    assert yes_s.triggers == ("Forecast würde sich ändern bei: die Abstimmung im Senat.",)


def test_simple_binary_market_gets_minimal_honest_scenarios():
    semantics = ResolutionSemantics(
        yes_condition="the event described by the question actually occurs",
        no_condition="the event does not occur (status quo continues)",
        deadline=None, measurement=None, threshold=None, required_source=None,
    )
    result = build_scenarios(
        estimated_yes_probability=0.55, submodel_estimates=[], news_evidence=[],
        comparable_sample_size=3, recommendation="WATCH_YES",
        resolution_path=None, resolution_semantics=semantics,
        evidence_for_yes=(), evidence_for_no=(), change_triggers=(),
    )
    assert len(result.scenarios) == 2
    yes_s = next(s for s in result.scenarios if s.outcome == "YES")
    no_s = next(s for s in result.scenarios if s.outcome == "NO")
    assert yes_s.description == "YES: the event described by the question actually occurs"
    assert no_s.description == "NO: the event does not occur (status quo continues)"
    # No multi-step richness fabricated for a market with no real structure.
    assert yes_s.necessary_events == ()
    assert yes_s.probability is None


def test_market_with_neither_structure_gets_honestly_empty_scenarios():
    result = build_scenarios(
        estimated_yes_probability=None, submodel_estimates=[], news_evidence=[],
        comparable_sample_size=0, recommendation="INSUFFICIENT_DATA",
    )
    assert result.scenarios == ()


def test_resolution_path_wins_over_binary_when_both_present():
    """A real multi-step structure is strictly richer information than a
    generic yes/no condition string — when both exist, the richer form is
    used, not silently dropped in favor of the minimal one."""
    path = ResolutionPath(applies=True, steps=(ResolutionStep(name="introduced", status="unknown"),))
    semantics = ResolutionSemantics(
        yes_condition="fallback yes", no_condition="fallback no", deadline=None,
        measurement=None, threshold=None, required_source=None,
    )
    result = build_scenarios(
        estimated_yes_probability=0.5, submodel_estimates=[], news_evidence=[],
        comparable_sample_size=0, recommendation="NO_BET",
        resolution_path=path, resolution_semantics=semantics,
    )
    assert any("Einbringung" in s.description for s in result.scenarios)
    assert not any("fallback yes" in s.description for s in result.scenarios)


# --- Part 2: GPT presentation layer ----------------------------------------


def _seed_market(storage: Storage) -> str:
    market = Market(
        provider="polymarket", provider_market_id="1", condition_id="", question="Will Team A win?",
        slug="team-a", category="esports", liquidity=100000, volume_24h=20000, yes_price=0.5,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = '1'"
    ).fetchone()[0]


def test_payload_carries_block_a_e_structured_fields(tmp_path: Path):
    """Confirms the input-assembly staleness finding is fixed: the payload
    handed to GPT now contains published_forecast_probability_percent,
    forecast_status, forecast_maturity, decision_state, and change_triggers
    — none of which the old payload builder ever included."""
    storage = Storage(tmp_path / "test.db")
    market_id = _seed_market(storage)
    market = ai_service._load_market_row(storage, market_id)
    yes_price, liquidity, _ = ai_service._latest_snapshot(storage, market_id)
    from polymarketpulse.prediction import compute_prediction

    prediction = compute_prediction(
        storage.connection, market_id=market_id, provider=market["provider"],
        provider_market_id=market["provider_market_id"], category=market["category"],
        classified_category=market.get("classified_category"), market_yes_price=yes_price,
        liquidity=liquidity, data_quality_report_score=None, news_count=0, news_agreement=None,
        resolution_rules_present=False, question=market["question"] or "", resolution_text=None,
    )
    payload = ai_service._build_recommendation_payload(market, prediction, ["market_price"])
    for key in (
        "published_forecast_probability_percent", "forecast_status", "forecast_maturity",
        "decision_state", "decision_reasons", "change_triggers", "divergence_audit_verdict",
        "independent_probability_percent",
    ):
        assert key in payload
    # Values are the real engine values, not placeholders.
    assert payload["forecast_status"] == prediction.forecast_status
    assert payload["forecast_maturity"] == prediction.forecast_maturity
    assert payload["decision_state"] == prediction.decision_state
    assert payload["change_triggers"] == list(prediction.change_triggers)


def test_fabricated_change_trigger_in_mocked_response_is_overwritten_not_trusted(tmp_path: Path):
    """The core discipline check: a MOCKED model response that invents a
    change_triggers entry never present in the real prediction must not
    survive into the persisted/returned explanation — it is unconditionally
    overwritten with the real prediction.change_triggers tuple."""
    storage = Storage(tmp_path / "test.db")
    base = Settings.load()
    settings = replace(
        base, database_path=tmp_path / "test.db", ai_enabled=True, openai_api_key="sk-fake-test-key",
        openai_model="gpt-5-nano", openai_fallback_model="gpt-5-mini", ai_cache_ttl_seconds=900,
    )
    market_id = _seed_market(storage)
    market = ai_service._load_market_row(storage, market_id)
    yes_price, liquidity, _ = ai_service._latest_snapshot(storage, market_id)
    from polymarketpulse.prediction import compute_prediction

    prediction = compute_prediction(
        storage.connection, market_id=market_id, provider=market["provider"],
        provider_market_id=market["provider_market_id"], category=market["category"],
        classified_category=market.get("classified_category"), market_yes_price=yes_price,
        liquidity=liquidity, data_quality_report_score=None, news_count=0, news_agreement=None,
        resolution_rules_present=False, question=market["question"] or "", resolution_text=None,
    )

    fabricated = ExplanationResult(
        direction=direction_for(prediction.recommendation),
        recommendation=prediction.recommendation,
        headline="Test", summary="Test summary",
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
        what_we_know="Test",
        divergence_explanation="Test",
        # This is the fabrication: an invented trigger the real engine never
        # produced for this market.
        change_triggers=["FABRICATED: Putin resigns tomorrow (invented by the model)"],
    ).model_dump()

    class ScriptedClient:
        def __init__(self, payload):
            self.payload = payload
            self.calls = 0

        def generate_structured(self, system_prompt, user_prompt, schema_model, schema_name):
            self.calls += 1
            return self.payload, 500, 100

    nano = ScriptedClient(fabricated)
    response = ai_service.explain_recommendation(storage, settings, market_id, nano_client=nano)

    assert nano.calls == 1
    assert response.explanation.change_triggers == list(prediction.change_triggers)
    assert "FABRICATED" not in " ".join(response.explanation.change_triggers)
