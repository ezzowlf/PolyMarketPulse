"""Phase G tests — LLM-assist semantics layer. Every LLM call in this file
is a FakeClient (see test_ai_service.py's own FakeClient pattern) — no
network call, live or otherwise, is ever made here."""

from dataclasses import replace

import pytest

from polymarketpulse.config import Settings
from polymarketpulse.prediction import llm_semantics
from polymarketpulse.prediction.semantics import (
    EvidenceRelation,
    ExtractedEvent,
    MarketProposition,
    classify_evidence_relation,
    extract_event,
    parse_market_proposition,
)


class FakeClient:
    """Stands in for OpenAIStructuredClient — never touches the network."""

    def __init__(self, payload: dict):
        self.calls = 0
        self.payload = payload

    def generate_structured(self, system_prompt, user_prompt, schema_model, schema_name):
        self.calls += 1
        return self.payload, 5, 7


class ExplodingClient:
    """Simulates any AI-layer failure — must never crash the caller."""

    def __init__(self):
        self.calls = 0

    def generate_structured(self, system_prompt, user_prompt, schema_model, schema_name):
        self.calls += 1
        raise RuntimeError("network exploded")


@pytest.fixture
def enabled_settings() -> Settings:
    base = Settings.load()
    return replace(
        base,
        ai_enabled=True,
        openai_api_key="sk-fake-test-key",
        llm_semantics_enabled=True,
    )


@pytest.fixture
def disabled_settings() -> Settings:
    base = Settings.load()
    # llm_semantics_enabled default False (never overridden here) — the
    # exact "feature flag off" state this task must guarantee is a no-op.
    return replace(base, ai_enabled=True, openai_api_key="sk-fake-test-key")


# --- Proposition parsing ----------------------------------------------------


def _ambiguous_proposition() -> MarketProposition:
    # A question with no confidently-parsable subject/event_type — real
    # rule-based ambiguity, not a contrived one.
    return parse_market_proposition("What happens next in the situation?", None)


def _confident_proposition() -> MarketProposition:
    return parse_market_proposition("Will Trump resign as President by August 31, 2026?", None)


def test_llm_semantics_disabled_by_default() -> None:
    settings = Settings.load()
    assert settings.llm_semantics_enabled is False


def test_proposition_assist_flag_off_is_noop(disabled_settings: Settings) -> None:
    prop = _ambiguous_proposition()
    assert prop.proposition_status == "AMBIGUOUS"
    result = llm_semantics.llm_assist_proposition_parse(
        "What happens next in the situation?", None, prop, disabled_settings
    )
    assert result is None


def test_proposition_assist_never_called_on_confident_result(enabled_settings: Settings) -> None:
    prop = _confident_proposition()
    assert prop.proposition_status == "CLEAR"
    client = FakeClient({"subject": "Someone Else", "event_type": "office_departure",
                          "direction": "yes_if_occurs", "resolved_ambiguity": True, "rationale": "x"})
    result = llm_semantics.llm_assist_proposition_parse(
        "Will Trump resign as President by August 31, 2026?", None, prop, enabled_settings, client=client
    )
    assert result is None
    assert client.calls == 0


def test_proposition_assist_resolves_genuine_ambiguity(enabled_settings: Settings) -> None:
    prop = _ambiguous_proposition()
    assert prop.proposition_status == "AMBIGUOUS"
    client = FakeClient({
        "subject": "The Committee", "event_type": "legislation", "direction": "yes_if_occurs",
        "resolved_ambiguity": True, "rationale": "Text implies a legislative vote outcome.",
    })
    result = llm_semantics.llm_assist_proposition_parse(
        "What happens next in the situation?", None, prop, enabled_settings, client=client
    )
    assert client.calls == 1
    assert result is not None
    assert result.subject == "The Committee"
    assert result.event_type == "legislation"
    assert result.proposition_status == "CLEAR"


def test_proposition_assist_invalid_response_falls_back(enabled_settings: Settings) -> None:
    prop = _ambiguous_proposition()
    # Missing required "rationale" field -> fails Pydantic validation inside
    # the LLM client's own model_validate, or ours -> must fall back safely.
    client = FakeClient({"subject": "X", "event_type": "election", "direction": "yes_if_occurs",
                          "resolved_ambiguity": True})
    result = llm_semantics.llm_assist_proposition_parse(
        "What happens next in the situation?", None, prop, enabled_settings, client=client
    )
    assert result is None


def test_proposition_assist_call_failure_falls_back(enabled_settings: Settings) -> None:
    prop = _ambiguous_proposition()
    client = ExplodingClient()
    result = llm_semantics.llm_assist_proposition_parse(
        "What happens next in the situation?", None, prop, enabled_settings, client=client
    )
    assert result is None
    assert client.calls == 1


def test_proposition_assist_unresolved_ambiguity_falls_back(enabled_settings: Settings) -> None:
    prop = _ambiguous_proposition()
    client = FakeClient({"subject": None, "event_type": None, "direction": "unknown",
                          "resolved_ambiguity": False, "rationale": "Still unclear."})
    result = llm_semantics.llm_assist_proposition_parse(
        "What happens next in the situation?", None, prop, enabled_settings, client=client
    )
    assert result is None


# --- Evidence relation classification --------------------------------------


def _ambiguous_evidence_pair() -> tuple[MarketProposition, ExtractedEvent, EvidenceRelation]:
    # Subject-less, same-family (topic_ok True) pairing but with
    # direction="unknown" — classify_evidence_relation's whole
    # yes_if_occurs branch is skipped, and with sentiment/link_confidence
    # both below the weak-tier gate it falls straight through to the real
    # AMBIGUOUS fallback (see semantics.py's final `return EvidenceRelation(
    # "AMBIGUOUS", ...)`), a genuine rule-based-can't-decide case.
    proposition = MarketProposition(
        subject=None, predicate="war_escalation", object=None, event_type="war_escalation",
        direction="unknown", threshold=None, unit=None, location=None, start_time=None, deadline=None,
        yes_condition="resolves YES if the conflict escalates", no_condition="resolves NO otherwise",
        resolution_authority=None, ambiguity_flags=(), proposition_status="CLEAR",
    )
    event = ExtractedEvent(
        actors=(), action="escalation", target="escalate", event_type="war_escalation", location=None,
        event_time=None, expected_time=None, status="actual", source=None, source_type=None, certainty="reported",
    )
    relation = classify_evidence_relation(proposition, event, sentiment=0.0, link_confidence=0.1)
    return proposition, event, relation


def test_evidence_assist_flag_off_is_noop(disabled_settings: Settings) -> None:
    proposition, event, relation = _ambiguous_evidence_pair()
    result = llm_semantics.llm_assist_evidence_relation(proposition, event, relation, disabled_settings)
    assert result is None


def test_evidence_assist_never_called_on_confident_result(enabled_settings: Settings) -> None:
    proposition = parse_market_proposition("Will Trump resign as President by August 31, 2026?", None)
    event = extract_event("Trump resigns as President, effective immediately")
    relation = classify_evidence_relation(proposition, event, sentiment=0.0, link_confidence=0.9)
    assert relation.label != "AMBIGUOUS"
    client = FakeClient({"label": "DIRECT_NO", "entailment": "CONTRADICTS", "resolved_ambiguity": True, "rationale": "x"})
    result = llm_semantics.llm_assist_evidence_relation(proposition, event, relation, enabled_settings, client=client)
    assert result is None
    assert client.calls == 0


def test_evidence_assist_resolves_genuine_ambiguity(enabled_settings: Settings) -> None:
    proposition, event, relation = _ambiguous_evidence_pair()
    assert relation.label == "AMBIGUOUS"
    client = FakeClient({
        "label": "WEAK_YES", "entailment": "ENTAILS", "resolved_ambiguity": True,
        "rationale": "Tension commentary weakly supports continued escalation.",
    })
    result = llm_semantics.llm_assist_evidence_relation(proposition, event, relation, enabled_settings, client=client)
    assert client.calls == 1
    assert result is not None
    assert result.label == "WEAK_YES"
    assert result.entailment == "ENTAILS"
    assert result.quantitative_weight == 0.15


def test_evidence_assist_invalid_response_falls_back(enabled_settings: Settings) -> None:
    proposition, event, relation = _ambiguous_evidence_pair()
    client = FakeClient({"label": "NOT_A_REAL_LABEL", "entailment": "ENTAILS", "resolved_ambiguity": True, "rationale": "x"})
    result = llm_semantics.llm_assist_evidence_relation(proposition, event, relation, enabled_settings, client=client)
    assert result is None


def test_evidence_assist_call_failure_falls_back(enabled_settings: Settings) -> None:
    proposition, event, relation = _ambiguous_evidence_pair()
    client = ExplodingClient()
    result = llm_semantics.llm_assist_evidence_relation(proposition, event, relation, enabled_settings, client=client)
    assert result is None
    assert client.calls == 1


def test_evidence_assist_still_ambiguous_response_falls_back(enabled_settings: Settings) -> None:
    proposition, event, relation = _ambiguous_evidence_pair()
    client = FakeClient({"label": "AMBIGUOUS", "entailment": "NEUTRAL", "resolved_ambiguity": True, "rationale": "still unclear"})
    result = llm_semantics.llm_assist_evidence_relation(proposition, event, relation, enabled_settings, client=client)
    assert result is None
