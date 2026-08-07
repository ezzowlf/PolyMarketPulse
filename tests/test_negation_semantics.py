"""K4: negation/adversarial semantics coverage for classify_evidence_relation
/ extract_event. These are real gaps found by auditing the existing Phase A
regression tests (tests/test_semantics.py) against the specific adversarial
phrasings called out in the statistical-honesty spec: a headline containing
an event-family keyword (e.g. "ceasefire") must not collapse to the same
evidence direction regardless of whether it reports the event happening,
failing, being merely proposed, or ending.

Two real bugs were found and fixed in semantics.py as part of this test:

1. "Ceasefire not agreed after talks" / "Trump will not resign, aide says"
   were read as the event HAVING occurred, because the original
   `_NEGATION_TERMS` list only covered single negation words ("denied",
   "failed", ...) and missed "not <verb>" / "will not <verb>" phrasings.
2. "Ceasefire proposal submitted" / "Ceasefire expected next week" /
   "Ceasefire expires at midnight" were all read as confidently as a
   confirmed ceasefire, because `_STATUS_BY_ACTION` hardcoded
   escalation/deescalation status to "actual" regardless of certainty, and
   the bare keyword "ceasefire" in _DEESCALATION_TERMS matched an existing
   ceasefire *ending* the same as one being newly reached.
"""

from __future__ import annotations

from polymarketpulse.prediction.news import score_sentiment
from polymarketpulse.prediction.semantics import (
    classify_evidence_relation,
    extract_event,
    parse_market_proposition,
)


def _classify(question: str, title: str, link_confidence: float = 0.6):
    proposition = parse_market_proposition(question, None)
    event = extract_event(title)
    sentiment, _ = score_sentiment(title)
    relation = classify_evidence_relation(proposition, event, sentiment, link_confidence)
    return event, relation


_CEASEFIRE_Q = "Will a ceasefire occur?"


def test_ceasefire_agreed_supports_yes() -> None:
    _, relation = _classify(_CEASEFIRE_Q, "Ceasefire agreed between sides")
    assert relation.label in ("DIRECT_YES", "SUPPORTS_YES")
    assert relation.entailment == "ENTAILS"


def test_ceasefire_denied_supports_no() -> None:
    _, relation = _classify(_CEASEFIRE_Q, "Ceasefire denied by officials")
    assert relation.label in ("DIRECT_NO", "SUPPORTS_NO")
    assert relation.entailment == "CONTRADICTS"


def test_ceasefire_not_agreed_supports_no_not_yes() -> None:
    """Regression for the 'not agreed' negation gap — previously matched
    the bare 'ceasefire' keyword and was wrongly read as SUPPORTS_YES."""
    event, relation = _classify(_CEASEFIRE_Q, "Ceasefire not agreed after talks")
    assert event.event_type == "war_escalation"
    assert relation.label in ("DIRECT_NO", "SUPPORTS_NO")
    assert relation.entailment == "CONTRADICTS"


def test_talks_failed_supports_no() -> None:
    _, relation = _classify(_CEASEFIRE_Q, "Peace talks failed")
    assert relation.label in ("DIRECT_NO", "SUPPORTS_NO")
    assert relation.entailment == "CONTRADICTS"


def test_ceasefire_proposal_submitted_is_not_confirmed_yes() -> None:
    """A merely-proposed ceasefire must not classify identically to a
    confirmed one."""
    event, relation = _classify(_CEASEFIRE_Q, "Ceasefire proposal submitted")
    assert event.status == "intent"
    assert relation.label != "DIRECT_YES"


def test_expected_ceasefire_is_not_confirmed_yes() -> None:
    event, relation = _classify(_CEASEFIRE_Q, "Ceasefire expected next week")
    assert event.status == "intent"
    assert relation.label != "DIRECT_YES"


def test_ceasefire_expires_supports_no_not_yes() -> None:
    """An existing ceasefire ENDING is the opposite signal from one being
    newly reached — must not collapse to the same SUPPORTS_YES direction as
    'ceasefire agreed'."""
    event, relation = _classify(_CEASEFIRE_Q, "Ceasefire expires at midnight")
    assert event.event_type == "war_escalation"
    assert relation.label not in ("DIRECT_YES", "SUPPORTS_YES")


_RESIGN_Q = "Will Trump resign?"


def test_resigns_supports_yes() -> None:
    _, relation = _classify(_RESIGN_Q, "Trump resigns effective immediately")
    assert relation.label in ("DIRECT_YES", "SUPPORTS_YES")
    assert relation.entailment == "ENTAILS"


def test_denies_resignation_rumors_supports_no() -> None:
    _, relation = _classify(_RESIGN_Q, "Trump denies resignation rumors")
    assert relation.entailment == "CONTRADICTS"


def test_will_not_resign_supports_no_not_yes() -> None:
    """Regression for the 'will not <verb>' negation gap — previously
    matched the bare 'resign' keyword and was wrongly read as SUPPORTS_YES."""
    event, relation = _classify(_RESIGN_Q, "Trump will not resign, aide says")
    assert event.status == "continuation"
    assert relation.label not in ("DIRECT_YES", "SUPPORTS_YES")
    assert relation.entailment != "ENTAILS"


def test_ceasefire_and_denial_produce_different_relation_labels() -> None:
    """Direct adversarial-pair check: identical event family keyword, one
    asserting the event occurred and one denying it, must not collapse to
    the same relation label."""
    _, agreed = _classify(_CEASEFIRE_Q, "Ceasefire agreed between sides")
    _, denied = _classify(_CEASEFIRE_Q, "Ceasefire not agreed after talks")
    assert agreed.label != denied.label
    assert agreed.entailment != denied.entailment
