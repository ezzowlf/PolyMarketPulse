"""Part 1 (Live Evidence Engine continuation): direction-aware waterway
evidence classification regression tests.

Round 3's audit found a real correctness bug waiting to happen: two real
waterway markets (`2774056` Hormuz "returns to normal" and
`polymarket:2911874` Bab el-Mandeb "effectively closed") share the exact
same `event_type == "strategic_waterway"`. A naive event_type-equality
classifier would read the SAME headline as DIRECT_YES for both markets
(or DIRECT_NO for both), which is wrong for one of them by construction.
These tests prove `classify_evidence_relation` now consults each market's
own `target_waterway_state` (via `MarketProposition`, populated by
`parse_market_proposition` reusing `world_state._classify_waterway_headline`)
instead of just comparing event_type."""

from polymarketpulse.prediction.semantics import (
    classify_evidence_relation,
    extract_event,
    parse_market_proposition,
)

CLOSURE_QUESTION = "Will the Strait of Hormuz be effectively closed by December 31?"
NORMALIZATION_QUESTION = "Will Strait of Hormuz traffic return to normal by December 31?"

CLOSED_HEADLINE = "Strait of Hormuz officially closed after military strike, sources say shipping halted"
NORMAL_HEADLINE = "Strait of Hormuz traffic officially returns to normal after ceasefire"
NEUTRAL_HEADLINE = "Rally held near Strait of Hormuz to protest sanctions"


def _classify(question: str, headline: str):
    proposition = parse_market_proposition(question, None)
    assert proposition.event_type == "strategic_waterway"
    event = extract_event(headline)
    return classify_evidence_relation(proposition, event, sentiment=0.0, link_confidence=0.5, title=headline)


def test_propositions_have_correct_target_waterway_state():
    closure_prop = parse_market_proposition(CLOSURE_QUESTION, None)
    normal_prop = parse_market_proposition(NORMALIZATION_QUESTION, None)
    assert closure_prop.target_waterway_state == "CLOSED"
    assert normal_prop.target_waterway_state == "NORMAL"


def test_closed_headline_opposite_direction_for_same_event_type():
    """(a) Same headline ('closed'), same event_type on both markets, but
    opposite classified relation depending on which market it's evaluated
    against — the exact bug class Round 3 flagged."""
    closure_relation = _classify(CLOSURE_QUESTION, CLOSED_HEADLINE)
    normalization_relation = _classify(NORMALIZATION_QUESTION, CLOSED_HEADLINE)

    assert closure_relation.label == "DIRECT_YES"
    assert closure_relation.entailment == "ENTAILS"
    assert normalization_relation.label == "DIRECT_NO"
    assert normalization_relation.entailment == "CONTRADICTS"


def test_normal_headline_opposite_direction_for_same_event_type():
    """(b) The reverse of (a): a 'returns to normal' headline."""
    closure_relation = _classify(CLOSURE_QUESTION, NORMAL_HEADLINE)
    normalization_relation = _classify(NORMALIZATION_QUESTION, NORMAL_HEADLINE)

    assert normalization_relation.label == "DIRECT_YES"
    assert normalization_relation.entailment == "ENTAILS"
    assert closure_relation.label == "DIRECT_NO"
    assert closure_relation.entailment == "CONTRADICTS"


def test_neutral_waterway_adjacent_headline_is_context_or_irrelevant():
    """(c) A generically Hormuz-adjacent headline that says nothing about
    traffic/shipping status must not be misread as DIRECT anything."""
    closure_relation = _classify(CLOSURE_QUESTION, NEUTRAL_HEADLINE)
    normalization_relation = _classify(NORMALIZATION_QUESTION, NEUTRAL_HEADLINE)

    assert closure_relation.label in ("CONTEXT", "IRRELEVANT", "AMBIGUOUS")
    assert normalization_relation.label in ("CONTEXT", "IRRELEVANT", "AMBIGUOUS")
    assert closure_relation.entailment == "NEUTRAL"
    assert normalization_relation.entailment == "NEUTRAL"


def test_backward_compatible_when_title_not_passed():
    """Existing callers that don't pass `title` (tests/other call sites)
    keep their prior (pre-Part-1) behaviour — the waterway branch is
    opt-in via the new optional parameter, not a silent default change."""
    proposition = parse_market_proposition(CLOSURE_QUESTION, None)
    event = extract_event(CLOSED_HEADLINE)
    relation = classify_evidence_relation(proposition, event, sentiment=0.0, link_confidence=0.5)
    # Without title, falls through to the generic same/opposite event_type
    # logic. event.event_type is None here (no waterway action family
    # exists in _ACTION_FAMILIES — this was the exact Round 3 finding), so
    # relation_kind is "none" and this is topic-gated to IRRELEVANT/CONTEXT
    # rather than crashing or guessing DIRECT.
    assert relation.label in ("CONTEXT", "IRRELEVANT", "AMBIGUOUS")
