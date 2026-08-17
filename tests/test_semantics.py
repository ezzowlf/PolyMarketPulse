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


# ---------------------------------------------------------------------------
# E9: extended event_type detection coverage — closes the wiring gap where
# geopolitics.py/macro.py/politics.py/sports.py existed but _detect_event_type
# could never actually produce the event_type strings those models check for.
# ---------------------------------------------------------------------------


def test_detects_ceasefire_event_type() -> None:
    """'Will a ceasefire occur?' must produce geopolitics.py's exact
    expected string ('ceasefire'), not a generic placeholder."""
    proposition = parse_market_proposition("Will a ceasefire occur?", None)
    assert proposition.event_type == "ceasefire"


def test_detects_ceasefire_holds_vs_fighting_resumes_are_different_event_types() -> None:
    """'Will the ceasefire hold?' talks about a ceasefire; 'Will fighting
    resume?' talks about renewed conflict — different propositions, and
    must not collapse onto the same generic bucket."""
    holds = parse_market_proposition("Will the ceasefire hold through August?", None)
    resumes = parse_market_proposition("Will fighting resume this month?", None)
    assert holds.event_type == "ceasefire"
    assert resumes.event_type == "war_escalation"
    assert holds.event_type != resumes.event_type


def test_detects_war_escalation_event_type() -> None:
    proposition = parse_market_proposition("Will there be a major military offensive this month?", None)
    assert proposition.event_type == "war_escalation"


def test_detects_sanctions_event_type() -> None:
    proposition = parse_market_proposition("Will new sanctions be imposed on the regime?", None)
    assert proposition.event_type == "sanctions"


def test_detects_territorial_control_event_type() -> None:
    proposition = parse_market_proposition("Will the army gain territorial control of the region?", None)
    assert proposition.event_type == "territorial_control"


def test_detects_strategic_waterway_event_type() -> None:
    proposition = parse_market_proposition("Will the strait remain blockaded through Q3?", None)
    assert proposition.event_type == "strategic_waterway"


def test_detects_diplomatic_agreement_event_type() -> None:
    proposition = parse_market_proposition("Will a diplomatic agreement be reached between the two sides?", None)
    assert proposition.event_type == "diplomatic_agreement"


def test_detects_military_action_event_type() -> None:
    proposition = parse_market_proposition("Will there be a military strike on the facility?", None)
    assert proposition.event_type == "military_action"


def test_detects_rate_cut_event_type() -> None:
    proposition = parse_market_proposition("Will the Fed cut rates at the next meeting?", None)
    assert proposition.event_type == "rate_cut"


def test_detects_rate_hike_event_type() -> None:
    proposition = parse_market_proposition("Will the ECB raise rates this quarter?", None)
    assert proposition.event_type == "rate_hike"


def test_detects_rate_hold_event_type() -> None:
    proposition = parse_market_proposition("Will the Fed keep rates unchanged at the FOMC meeting?", None)
    assert proposition.event_type == "rate_hold"


def test_rate_decision_requires_central_bank_context() -> None:
    """A generic 'will prices stay unchanged' question with no central-bank
    context must not be misread as a rate-hold market — the central-bank
    keyword co-occurrence requirement exists precisely to avoid this."""
    proposition = parse_market_proposition("Will grocery prices stay unchanged this month?", None)
    assert proposition.event_type != "rate_hold"


def test_detects_legislation_event_type() -> None:
    proposition = parse_market_proposition("Will Congress pass the infrastructure bill this year?", None)
    assert proposition.event_type == "legislation"


def test_detects_legislation_signed_into_law() -> None:
    proposition = parse_market_proposition("Will the act be signed into law by December?", None)
    assert proposition.event_type == "legislation"


def test_detects_election_winner_event_type() -> None:
    proposition = parse_market_proposition("Who will win the election?", None)
    assert proposition.event_type == "election"


def test_election_distinct_from_office_departure() -> None:
    """Winning an election is not the same proposition as an incumbent
    leaving office — these must not collapse onto the same event_type."""
    election = parse_market_proposition("Will Smith win the election?", None)
    departure = parse_market_proposition("Will Smith resign from office?", None)
    assert election.event_type == "election"
    assert departure.event_type == "office_departure"
    assert election.event_type != departure.event_type


def test_detects_appointment_event_type() -> None:
    proposition = parse_market_proposition("Will the nominee be confirmed by Senate?", None)
    assert proposition.event_type == "appointment"


def test_detects_court_outcome_event_type() -> None:
    proposition = parse_market_proposition("Will the Supreme Court rule in favor of the plaintiff?", None)
    assert proposition.event_type == "court_outcome"


def test_detects_sport_tournament_event_type() -> None:
    proposition = parse_market_proposition("Will Team A win the championship this season?", None)
    assert proposition.event_type == "sport_tournament"


def test_detects_sport_qualification_event_type() -> None:
    proposition = parse_market_proposition("Will Team A qualify for the playoffs?", None)
    assert proposition.event_type == "sport_qualification"


def test_detects_sport_match_event_type() -> None:
    proposition = parse_market_proposition("Lakers vs Celtics: who wins tonight's game?", None)
    assert proposition.event_type == "sport_match"


def test_existing_office_departure_detection_not_regressed() -> None:
    proposition = parse_market_proposition(_TRUMP_QUESTION, None)
    assert proposition.event_type == "office_departure"


def test_existing_price_threshold_detection_not_regressed() -> None:
    proposition = parse_market_proposition("Will Bitcoin be above $200,000 by December 31?", None)
    assert proposition.event_type == "price_above"


# ---------------------------------------------------------------------
# WTI $85 Golden Case (Block A/B/C) — the real production market
# polymarket:3310013: "Will WTI Crude Oil (WTI) hit (HIGH) $85 in
# August?", resolution rule confirmed to mean "does the Active Month WTI
# futures 1-minute-candle High touch/exceed $85 at ANY point during
# August's trading sessions" (a barrier/touch claim, Pyth primary /
# CME fallback per the real resolution text) — never "does WTI close
# above $85 at the end of August" (a terminal claim). Before this round's
# fix, this exact question produced asset=None, threshold=None,
# proposition_status=AMBIGUOUS (root cause: no WTI/commodity asset alias,
# the "(HIGH)" annotation broke the threshold-number regex, and "in
# August" phrasing wasn't recognized as a deadline at all).
# ---------------------------------------------------------------------

_WTI_QUESTION = "Will WTI Crude Oil (WTI) hit (HIGH) $85 in August?"


def test_wti_golden_case_full_semantic_parse() -> None:
    p = parse_market_proposition(_WTI_QUESTION, None)
    assert p.event_type == "price_above"
    assert p.asset == "WTI_CRUDE_OIL"
    assert p.asset_class == "COMMODITY"
    assert p.threshold == 85.0
    assert p.unit == "USD"
    assert p.barrier_field == "HIGH"
    assert p.deadline == "August"
    assert p.deadline_semantics == "by_deadline"  # touch/barrier, NOT terminal-at-deadline
    assert p.price_contract_type == "TOUCH_HIGH"
    assert p.proposition_status == "CLEAR"  # was AMBIGUOUS before this fix
    assert p.ambiguity_flags == ()


def test_wti_touch_low_variant() -> None:
    """The mirror case: a (LOW) annotation must produce TOUCH_LOW, not be
    conflated with TOUCH_HIGH just because "hit" is direction-neutral on
    its own."""
    p = parse_market_proposition("Will WTI Crude Oil (WTI) hit (LOW) $60 in August?", None)
    assert p.barrier_field == "LOW"
    assert p.price_contract_type == "TOUCH_LOW"
    assert p.threshold == 60.0


def test_price_threshold_touch_vs_terminal_stays_distinct() -> None:
    """"Reach $X by <date>" (barrier/touch, an explicit "by" preposition)
    must NOT be conflated with "above $X on <date>" (terminal, an explicit
    "on" preposition) — the two real Polymarket contract shapes this
    project has always needed to keep apart (see the deadline_semantics
    module docstring), now also expressed via the new, more explicit
    price_contract_type vocabulary."""
    touch = parse_market_proposition("Will Bitcoin reach $200,000 by December 31?", None)
    assert touch.deadline_semantics == "by_deadline"
    assert touch.price_contract_type == "TOUCH_HIGH"

    terminal = parse_market_proposition("Will Bitcoin be above $200,000 on December 31?", None)
    assert terminal.deadline_semantics == "at_deadline"
    assert terminal.price_contract_type == "ABOVE_AT_DEADLINE"


def test_terminal_below_at_deadline() -> None:
    p = parse_market_proposition("Will Bitcoin be below $50,000 on December 31?", None)
    assert p.deadline_semantics == "at_deadline"
    assert p.price_contract_type == "BELOW_AT_DEADLINE"


def test_touch_verb_wins_over_by_preposition_absence() -> None:
    """A touch verb ("hit"/"touch"/"reach") implies barrier semantics even
    with no "by"/"on" date preposition at all -- only the weaker "in
    <Month>" phrasing is present, which alone would default to by_deadline
    anyway, but this proves the verb-based override doesn't accidentally
    flip it to terminal."""
    p = parse_market_proposition("Will Ethereum touch $10,000 in September?", None)
    assert p.deadline_semantics == "by_deadline"
    assert p.price_contract_type == "TOUCH_HIGH"
    assert p.asset == "ethereum"
    assert p.asset_class == "CRYPTO"


def test_commodity_asset_not_detected_without_price_threshold_phrasing() -> None:
    """A WTI-mentioning question with no recognizable threshold phrasing
    must not be misclassified as a price-threshold proposition at all --
    asset detection alone is not sufficient, matching the existing crypto
    behavior (_detect_price_direction requires both an asset AND a real
    threshold match)."""
    p = parse_market_proposition("Will WTI Crude Oil production increase this year?", None)
    assert p.event_type != "price_above"
    assert p.event_type != "price_below"
