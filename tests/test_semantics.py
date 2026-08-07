"""Permanent regression tests for the proposition/event-semantics layer
(prediction/semantics.py) — Phase A of replacing the sentiment-only
evidence classifier with a real "does this event entail/contradict this
specific proposition" model.

These are the exact fixture cases from the architecture review: a bare
actor-name match with unrelated action must never be scored as directional
evidence, a call-for-resignation must not be conflated with an actual
resignation, and identical text must classify differently depending on the
proposition it is being evaluated against (proving sentiment alone is not
driving the direction)."""

from __future__ import annotations

from polymarketpulse.prediction.news import score_sentiment
from polymarketpulse.prediction.semantics import (
    classify_evidence_relation,
    extract_event,
    parse_market_proposition,
)

_TRUMP_QUESTION = "Trump out as President by August 31?"


def _classify(question: str, resolution_text: str | None, title: str, link_confidence: float = 0.6):
    proposition = parse_market_proposition(question, resolution_text)
    event = extract_event(title)
    sentiment, _ = score_sentiment(title)
    relation = classify_evidence_relation(proposition, event, sentiment, link_confidence)
    return proposition, event, relation


def test_unrelated_positive_headline_about_subject_is_irrelevant_or_context() -> None:
    """The original regression case: 'Trump' mentioned positively in an
    unrelated context must not move the needle at all."""
    _, _, relation = _classify(
        _TRUMP_QUESTION, None, "President Trump and Republicans Deliver Big Wins for the Silver State"
    )
    assert relation.label in ("IRRELEVANT", "CONTEXT")
    assert relation.entailment == "NEUTRAL"
    assert relation.quantitative_weight == 0.0


def test_call_for_resignation_is_not_direct_yes() -> None:
    """A demand/call for resignation is not the resignation itself."""
    _, _, relation = _classify(_TRUMP_QUESTION, None, "Senator calls on Trump to resign")
    assert relation.label != "DIRECT_YES"
    assert relation.label in ("WEAK_YES", "CONTEXT")


def test_announced_resignation_with_effective_date_is_direct_yes() -> None:
    _, _, relation = _classify(
        _TRUMP_QUESTION, None, "Trump announces he will resign effective August 20"
    )
    assert relation.label == "DIRECT_YES"
    assert relation.entailment == "ENTAILS"
    assert relation.quantitative_weight > 0


def test_official_duty_schedule_supports_no() -> None:
    """Being scheduled for ongoing presidential business implies the
    subject is still in office — evidence AGAINST the departure resolving
    YES, not neutral and not YES-supporting."""
    _, _, relation = _classify(
        _TRUMP_QUESTION, None, "White House announces presidential events for Trump in September"
    )
    assert relation.label == "SUPPORTS_NO"
    assert relation.entailment == "CONTRADICTS"


def test_routine_rally_is_irrelevant_or_context() -> None:
    _, _, relation = _classify(_TRUMP_QUESTION, None, "Trump holds rally in Nevada")
    assert relation.label in ("IRRELEVANT", "CONTEXT")
    assert relation.quantitative_weight == 0.0


def test_same_headline_classifies_differently_by_proposition() -> None:
    """Sentiment-independence proof: the exact same positively-toned
    headline must be evaluated relative to what each market actually
    asserts, not its tone. 'Peace talks make excellent progress' supports
    a ceasefire market's YES condition while contradicting a war-escalation
    market's YES condition."""
    headline = "Peace talks make excellent progress"

    _, _, escalation_relation = _classify("Will war escalate?", None, headline)
    _, _, ceasefire_relation = _classify("Will a ceasefire occur?", None, headline)

    assert escalation_relation.label != ceasefire_relation.label
    assert escalation_relation.entailment != ceasefire_relation.entailment
    assert escalation_relation.entailment == "CONTRADICTS"
    assert ceasefire_relation.entailment == "ENTAILS"


def test_proposition_parser_marks_unparseable_question_ambiguous() -> None:
    proposition = parse_market_proposition("Will the sky be blue tomorrow?", None)
    assert proposition.proposition_status == "AMBIGUOUS"
    assert proposition.ambiguity_flags


def test_proposition_parser_uses_resolution_text_over_question_when_present() -> None:
    proposition = parse_market_proposition(
        "Trump out as President by August 31?",
        "This market resolves YES if Trump formally leaves office before August 31, 2026. "
        "It resolves NO if he remains in office past that date.",
    )
    assert "leaves" in proposition.yes_condition or "office" in proposition.yes_condition
    assert proposition.yes_condition != proposition.no_condition


def test_extract_event_leaves_action_none_when_unrecognized() -> None:
    event = extract_event("Local bakery wins award for best croissant")
    assert event.action is None
    assert event.event_type is None
