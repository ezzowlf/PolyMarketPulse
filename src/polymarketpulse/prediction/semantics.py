"""Proposition & Event Semantics — Phase A of the "real architecture" fix
for evidence.py's sentiment-only classifier.

The old failure mode: a headline that merely mentions the market's subject
entity ("Trump") in a positive tone was scored as YES-evidence for
"Trump out as President by August 31?" — sentiment about the subject is
not the same thing as evidence about the specific proposition the market
resolves on.

This module gives the engine two things it did not have before:

1. `parse_market_proposition` — turn the market's question/resolution text
   into a structured claim: who/what does the market actually assert, what
   would make it resolve YES, what would make it resolve NO.
2. `extract_event` — turn a news headline/body into a structured event:
   who did what, to whom, and how certain is that report.

`classify_evidence_relation` then asks the real question: does *this*
extracted event change the probability of *this* proposition's YES
condition — never "is this article's tone positive or negative". Sentiment
is used only as a last-resort, weak (WEAK_YES/WEAK_NO tier) signal, and
only when the event is already topically about the proposition's
predicate (actor + action-family overlap), never from a bare actor-name
match alone.

Everything here is rule-based (regex/keyword/date heuristics) — no LLM
calls, no network calls, fully deterministic and unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

EntailmentTag = Literal["ENTAILS", "CONTRADICTS", "NEUTRAL"]

EvidenceRelationLabel = Literal[
    "DIRECT_YES", "SUPPORTS_YES", "WEAK_YES",
    "DIRECT_NO", "SUPPORTS_NO", "WEAK_NO",
    "CONTEXT", "IRRELEVANT", "AMBIGUOUS",
]

# ---------------------------------------------------------------------------
# A1. Market Proposition Parser
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+")
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "to", "in", "on", "by", "for", "and", "or", "is", "are", "will",
        "be", "as", "at", "with", "from", "this", "that", "it", "its", "if", "than", "before",
        "after", "does", "do", "did", "has", "have", "had", "not", "no", "yes",
    }
)

# --- Event-type / predicate families ---------------------------------------
# Each family is a set of surface forms that, when found near the subject,
# indicate the market's predicate concerns that kind of event. Kept small
# and literal (auditable), extend by adding words.
_RESIGNATION_TERMS = (
    "resign", "resigns", "resigned", "resignation", "step down", "steps down", "stepped down",
    "stepping down", "out as president", "removed from office", "ousted", "impeached", "impeachment",
    "leaves office", "left office", "leave office", "quits", "quit",
)
_CALL_FOR_TERMS = (
    "calls on", "calls for", "urges", "demands", "pressure to resign", "should resign", "urged to",
    "asked to resign", "wants him to resign", "pushing for", "petition",
)
_ANNOUNCE_INTENT_TERMS = (
    "announces he will", "announces plans to", "will resign", "to resign effective", "intends to resign",
    "plans to resign", "announced his resignation",
)
# Official-duty activity implies active incumbency (weak evidence against
# an "office_departure" proposition resolving YES). Deliberately narrower
# than routine campaign/appearance activity below, which is too generic to
# say anything about the proposition either way.
_OFFICIAL_DUTY_TERMS = (
    "presidential events", "hosts", "signs executive order", "delivers remarks", "meets with",
    "state of the union", "oval office", "white house press briefing", "presidential schedule",
)
# Routine appearances (rallies, campaign stops, visits) — on-topic entity,
# but not informative about the proposition's predicate either direction.
_ROUTINE_ACTIVITY_TERMS = (
    "holds rally", "campaign event", "to visit", "plans to attend", "will attend", "scheduled to",
)
_ESCALATION_TERMS = (
    "escalate", "escalates", "escalation", "intensifies", "intensify", "offensive", "attack", "strikes",
    "invasion", "mobilizes", "mobilize", "airstrike", "shelling", "fighting resume", "fighting resumes",
    "fighting resumed", "fighting continues", "war breaks out", "war begins", "war erupts",
    "combat intensifies",
)
_DEESCALATION_TERMS = (
    "ceasefire", "cease-fire", "truce", "peace talks", "peace deal", "de-escalate", "de-escalation",
    "withdrawal", "withdraws troops", "agreement reached", "negotiated settlement", "armistice",
)

# E9: specific geopolitics sub-types that geopolitics.py's `_EVENT_TYPES`
# actually checks for (ceasefire/war_escalation/military_action/sanctions/
# territorial_control/strategic_waterway/diplomatic_agreement). These are
# deliberately checked BEFORE the generic escalation/deescalation terms
# above so that a more specific, on-topic phrasing ("sanctions imposed",
# "military strike") routes to the specialized sub-analysis instead of
# falling into the generic escalation/deescalation bucket. The generic
# terms remain as the fallback for text that only uses broad war/peace
# language without a more specific hook.
_SANCTIONS_TERMS = (
    "sanctions", "sanction", "travel ban", "asset freeze", "economic sanctions",
)
_STRATEGIC_WATERWAY_TERMS = (
    "strait", "canal", "waterway", "blockade", "blockades", "navigation closed",
    "navigation open", "shipping lane",
)
_TERRITORIAL_CONTROL_TERMS = (
    "territorial control", "controls territory", "control of territory", "captures territory",
    "seizes territory", "territory under", "cedes territory", "territory lost", "territory ceded",
    "annexes", "annexation",
)
_DIPLOMATIC_AGREEMENT_TERMS = (
    "diplomatic agreement", "diplomatic deal", "diplomatic accord", "treaty signed", "treaty reached",
    "pact reached", "accord reached", "bilateral agreement", "multilateral agreement",
)
_MILITARY_ACTION_TERMS = (
    "military strike", "military intervention", "military operation", "troops deployed",
    "troops entered", "ground invasion", "artillery shelling", "missile strike", "combat operation",
    "bombardment",
)

# E9: rate-decision, legislation, election, appointment, court and sports
# sub-vocabularies. Kept as narrow literal phrase lists like the rest of
# this module — each maps directly onto an event_type string the matching
# specialized model (macro.py / politics.py / sports.py) already checks
# for verbatim, so this is purely closing a detection gap, not inventing
# new downstream vocabulary.
_CENTRAL_BANK_TERMS = (
    "fed", "federal reserve", "ecb", "european central bank", "boj", "bank of japan",
    "boe", "bank of england", "snb", "swiss national bank", "central bank", "fomc",
    "rate decision", "policy meeting",
)
_RATE_CUT_QUESTION_TERMS = (
    "cut rates", "cut interest rates", "lower rates", "lowers rates", "rate cut",
    "reduce rates", "reduces rates", "reduce interest rates", "decrease rates",
    "decreases rates", "decrease interest rates", "lower policy rate", "lower the rate",
)
_RATE_HIKE_QUESTION_TERMS = (
    "hike rates", "hikes rates", "raise rates", "raises rates", "raise interest rates",
    "rate hike", "increase rates", "increases rates", "increase interest rates",
    "raise policy rate", "raise the rate",
)
_RATE_HOLD_QUESTION_TERMS = (
    "hold rates", "holds rates", "keep rates", "keeps rates", "maintain rates",
    "maintains rates", "rates unchanged", "rate unchanged", "no change in rates",
    "no change in", "pause rate", "stay unchanged", "stays unchanged", "rates steady",
    "keep rates steady",
)
_LEGISLATION_SUBJECT_TERMS = ("bill", "act", "legislation", "law", "amendment")
_LEGISLATION_ACTION_TERMS = (
    "pass", "passes", "passed", "signed into law", "becomes law", "enacted", "enact",
    "signed by the president", "sign into law",
)
_ELECTION_TERMS = (
    "win the election", "wins the election", "win the presidency", "wins the presidency",
    "who will win", "election winner", "elected president", "wins re-election",
    "win re-election", "wins the primary", "win the primary", "wins the race", "win the race",
)
_APPOINTMENT_TERMS = (
    "nominate", "nominated", "nomination", "nominee", "sworn in", "confirmed by senate",
    "confirmed by congress", "appoint", "appointed", "appointment confirmed",
)
_COURT_OUTCOME_TERMS = (
    "supreme court rule", "supreme court rules", "court rules", "judge rules",
    "court ruling", "court decision", "verdict", "court upholds", "court strikes down",
)
_SPORT_CONTEXT_TERMS = (
    "match", "game", "team", "tournament", "championship", "final", "playoff", "playoffs",
    "league", "cup", "series", "season", "coach", "roster", "vs", "vs.", "versus",
)
_SPORT_TOURNAMENT_TERMS = (
    "win the tournament", "wins the tournament", "tournament winner", "win the championship",
    "wins the championship", "champion of the tournament",
)
# "win the" ... "championship"/"tournament" often has intervening words in
# real questions ("win the 2026-27 UEFA Champions League Championship") —
# these looser co-occurrence markers catch that without requiring an exact
# contiguous phrase.
_SPORT_WIN_VERB_TERMS = ("win the", "wins the", "champion of")
_SPORT_TOURNAMENT_NOUN_TERMS = ("championship", "tournament")
_SPORT_WINNER_TERMS = (
    "win the final", "wins the final", "championship winner", "wins the title", "win the title",
)
_SPORT_QUALIFICATION_TERMS = (
    "qualify for", "qualifies for", "qualification for", "advance to the", "advances to the",
    "make the playoffs", "makes the playoffs", "fails to qualify", "does not qualify",
)
_SPORT_MATCH_PATTERN = re.compile(r"\b[A-Za-z][\w.'-]*(?:\s+[A-Za-z][\w.'-]*){0,2}\s+vs\.?\s+[A-Za-z]", re.IGNORECASE)

_YES_PATTERN = re.compile(
    r"resolves?\s+(?:to\s+)?[\"']?yes[\"']?\s+(?:if|when)\s+(.+?)(?:[.\n]|$)", re.IGNORECASE
)
_NO_PATTERN = re.compile(
    r"resolves?\s+(?:to\s+)?[\"']?no[\"']?\s+(?:if|when)\s+(.+?)(?:[.\n]|$)", re.IGNORECASE
)
_DEADLINE_PATTERN = re.compile(
    r"\bby\s+((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}(?:,\s*\d{4})?|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
# E5: distinguishes "reach threshold at ANY point before the deadline"
# (barrier/touch — matched by _DEADLINE_PATTERN's "by <date>" phrasing) from
# "threshold holds AT the deadline itself" (terminal — "on/at <date>",
# "as of <date>", "at the close of <date>"). Deliberately narrow/literal
# like the rest of this module: matches only explicit "on/at/as of" phrasing,
# never guesses. See MarketProposition.deadline_semantics.
_AT_DEADLINE_PATTERN = re.compile(
    r"\b(?:on|at|as of|at the close of)\s+"
    r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}(?:,\s*\d{4})?|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_THRESHOLD_PATTERN = re.compile(
    r"\b(above|below|over|under|at least|more than|less than|reach|reaches|hit|hits|exceed|exceeds)\s+\$?([\d.,]+)\s*(%|percent|k|thousand|million|m|billion|b)?",
    re.IGNORECASE,
)

# E4: quantitative price-threshold markets ("Will BTC be above $200,000 by
# Dec 31?"). Kept intentionally small/literal like the rest of this module —
# maps a handful of recognizable surface forms to a CoinGecko coin id, which
# is the one thing quant.py actually needs to go fetch a real price. Not
# meant to be an exhaustive asset list, just the assets this app can
# realistically get real, free, keyless price data for today.
_ASSET_ALIASES: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum", "ether": "ethereum",
    "solana": "solana", "sol": "solana",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "xrp": "ripple", "ripple": "ripple",
    "cardano": "cardano", "ada": "cardano",
}
_ASSET_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(_ASSET_ALIASES, key=len, reverse=True)) + r")\b", re.IGNORECASE
)
_ABOVE_DIRECTION_WORDS = frozenset({"above", "over", "at least", "more than", "reach", "reaches", "hit", "hits", "exceed", "exceeds"})
_BELOW_DIRECTION_WORDS = frozenset({"below", "under", "less than"})


def detect_price_asset(text: str) -> str | None:
    """Returns the CoinGecko coin id for the first recognized asset alias
    found in `text`, or None. Public so quant.py can reuse the exact same
    detection logic used during proposition parsing."""
    match = _ASSET_PATTERN.search(text)
    if not match:
        return None
    return _ASSET_ALIASES[match.group(1).lower()]


def _detect_price_direction(text: str) -> tuple[str | None, str]:
    """Returns (event_type, direction) for a numeric price/threshold claim:
    'price_above' when the question asks whether some quantity will be
    at/above a threshold, 'price_below' for at/below. Only fires when an
    asset alias is also present, so a generic "above 50%" polling question
    (no recognized asset) is correctly left alone for the existing
    keyword-based classifiers rather than being misread as a price bet."""
    if detect_price_asset(text) is None:
        return None, "unknown"
    threshold_match = _THRESHOLD_PATTERN.search(text)
    if not threshold_match:
        return None, "unknown"
    direction_word = threshold_match.group(1).lower()
    if direction_word in _ABOVE_DIRECTION_WORDS:
        return "price_above", "yes_if_occurs"
    if direction_word in _BELOW_DIRECTION_WORDS:
        return "price_below", "yes_if_occurs"
    return None, "unknown"


def _significant_terms(text: str, max_terms: int = 16) -> tuple[str, ...]:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    significant = [w for w in words if len(w) > 2 and w not in _STOPWORDS]
    seen: set[str] = set()
    ordered: list[str] = []
    for word in significant:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return tuple(ordered[:max_terms])


def _detect_event_type(text: str) -> tuple[str | None, str]:
    """Returns (event_type, direction) where direction is "yes_if_occurs"
    (the described event happening makes the proposition true) or
    "no_if_occurs" (the described event happening makes the proposition
    false / continues the status quo).

    E9: extended, in priority order, to cover the vocabulary the
    specialized models (geopolitics.py, macro.py, politics.py, sports.py)
    already expect verbatim. More specific sub-types are checked before
    their generic parent bucket so on-topic specific phrasing ("sanctions
    imposed", "military strike", "rate cut") wins over a generic
    war/peace or resignation match."""
    lowered = text.lower()

    # --- Politics: office departure (existing, unchanged priority) --------
    if any(t in lowered for t in _RESIGNATION_TERMS) or "out as president" in lowered:
        return "office_departure", "yes_if_occurs"

    # --- Politics: legislation, election, appointment, court outcome ------
    if any(subj in lowered for subj in _LEGISLATION_SUBJECT_TERMS) and any(
        act in lowered for act in _LEGISLATION_ACTION_TERMS
    ):
        return "legislation", "yes_if_occurs"
    if any(t in lowered for t in _ELECTION_TERMS):
        return "election", "yes_if_occurs"
    if any(t in lowered for t in _COURT_OUTCOME_TERMS):
        return "court_outcome", "yes_if_occurs"
    if any(t in lowered for t in _APPOINTMENT_TERMS):
        return "appointment", "yes_if_occurs"

    # --- Macro: central-bank rate decisions --------------------------------
    if any(cb in lowered for cb in _CENTRAL_BANK_TERMS):
        if any(t in lowered for t in _RATE_CUT_QUESTION_TERMS):
            return "rate_cut", "yes_if_occurs"
        if any(t in lowered for t in _RATE_HIKE_QUESTION_TERMS):
            return "rate_hike", "yes_if_occurs"
        if any(t in lowered for t in _RATE_HOLD_QUESTION_TERMS):
            return "rate_hold", "yes_if_occurs"

    # --- Geopolitics: specific sub-types before the generic bucket --------
    if any(t in lowered for t in _SANCTIONS_TERMS):
        return "sanctions", "yes_if_occurs"
    if any(t in lowered for t in _STRATEGIC_WATERWAY_TERMS):
        return "strategic_waterway", "yes_if_occurs"
    if any(t in lowered for t in _TERRITORIAL_CONTROL_TERMS):
        return "territorial_control", "yes_if_occurs"
    if any(t in lowered for t in _DIPLOMATIC_AGREEMENT_TERMS):
        return "diplomatic_agreement", "yes_if_occurs"
    if any(t in lowered for t in _MILITARY_ACTION_TERMS):
        return "military_action", "yes_if_occurs"
    if any(t in lowered for t in _DEESCALATION_TERMS):
        return "ceasefire", "yes_if_occurs"
    if any(t in lowered for t in _ESCALATION_TERMS):
        return "war_escalation", "yes_if_occurs"

    # --- Sports -------------------------------------------------------------
    if any(t in lowered for t in _SPORT_QUALIFICATION_TERMS) and any(
        s in lowered for s in _SPORT_CONTEXT_TERMS
    ):
        return "sport_qualification", "yes_if_occurs"
    if any(t in lowered for t in _SPORT_TOURNAMENT_TERMS) or (
        any(v in lowered for v in _SPORT_WIN_VERB_TERMS)
        and any(n in lowered for n in _SPORT_TOURNAMENT_NOUN_TERMS)
    ):
        return "sport_tournament", "yes_if_occurs"
    if any(t in lowered for t in _SPORT_WINNER_TERMS):
        return "sport_winner", "yes_if_occurs"
    if _SPORT_MATCH_PATTERN.search(text) and any(s in lowered for s in _SPORT_CONTEXT_TERMS):
        return "sport_match", "yes_if_occurs"

    price_event_type, price_direction = _detect_price_direction(text)
    if price_event_type is not None:
        return price_event_type, price_direction
    return None, "unknown"


def _extract_subject(question: str) -> str | None:
    # First capitalized run of words (naive proper-noun heuristic) — good
    # enough for "Trump out as President...", "Will Senator X resign...".
    match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", question)
    if match:
        candidate = match.group(1)
        if candidate.lower() not in ("will", "the", "is"):
            return candidate
    return None


@dataclass(frozen=True)
class MarketProposition:
    subject: str | None
    predicate: str | None
    object: str | None
    event_type: str | None
    direction: str | None  # "yes_if_occurs" | "no_if_occurs" | "unknown"
    threshold: float | None
    unit: str | None
    location: str | None
    start_time: str | None
    deadline: str | None
    yes_condition: str
    no_condition: str
    resolution_authority: str | None
    ambiguity_flags: tuple[str, ...] = field(default_factory=tuple)
    proposition_status: Literal["CLEAR", "AMBIGUOUS"] = "AMBIGUOUS"
    # E4: CoinGecko coin id when event_type is price_above/price_below and a
    # recognized asset alias was found in the question text (see
    # detect_price_asset). None for every other market — this field is
    # additive and never populated for non-price propositions.
    asset: str | None = None
    # E5: "by_deadline" (barrier/touch — threshold can be crossed at any
    # point before the deadline) vs "at_deadline" (terminal — threshold
    # must hold at the deadline itself). None when the phrasing doesn't
    # confidently indicate either — callers that need this distinction
    # (e.g. quant.py) must treat None as "ambiguous, do not guess".
    # Additive-only field; every non-price proposition just carries None.
    deadline_semantics: Literal["by_deadline", "at_deadline"] | None = None

    def as_dict(self) -> dict:
        return {
            "subject": self.subject, "predicate": self.predicate, "object": self.object,
            "event_type": self.event_type, "direction": self.direction, "threshold": self.threshold,
            "unit": self.unit, "location": self.location, "start_time": self.start_time,
            "deadline": self.deadline, "yes_condition": self.yes_condition, "no_condition": self.no_condition,
            "resolution_authority": self.resolution_authority, "ambiguity_flags": list(self.ambiguity_flags),
            "proposition_status": self.proposition_status, "asset": self.asset,
            "deadline_semantics": self.deadline_semantics,
        }


def parse_market_proposition(question: str, resolution_text: str | None) -> MarketProposition:
    """Rule-based structured parse of what a market actually asserts.
    `resolution_text` (an explicit "resolves YES if ..." clause) takes
    precedence over the raw question title whenever present."""
    ambiguity_flags: list[str] = []

    primary_text = resolution_text or question
    subject = _extract_subject(question)
    if subject is None:
        ambiguity_flags.append("no_subject_detected")

    event_type, direction = _detect_event_type(primary_text)
    if event_type is None:
        # fall back to trying the raw question too, in case resolution_text
        # was present but generic (e.g. only names a date)
        event_type, direction = _detect_event_type(question)
    if event_type is None:
        ambiguity_flags.append("no_event_type_detected")

    deadline_match = _DEADLINE_PATTERN.search(primary_text) or _DEADLINE_PATTERN.search(question)
    deadline = deadline_match.group(1) if deadline_match else None
    deadline_semantics: Literal["by_deadline", "at_deadline"] | None = None
    if deadline_match:
        deadline_semantics = "by_deadline"
    elif _AT_DEADLINE_PATTERN.search(primary_text) or _AT_DEADLINE_PATTERN.search(question):
        deadline_semantics = "at_deadline"

    threshold_match = _THRESHOLD_PATTERN.search(primary_text)
    threshold: float | None = None
    unit: str | None = None
    if threshold_match:
        try:
            threshold = float(threshold_match.group(2).replace(",", ""))
        except ValueError:
            threshold = None
        unit = "%" if threshold_match.group(3) else None

    yes_terms: tuple[str, ...] = ()
    no_terms: tuple[str, ...] = ()
    resolution_authority = None
    if resolution_text:
        yes_match = _YES_PATTERN.search(resolution_text)
        if yes_match:
            yes_terms = _significant_terms(yes_match.group(1))
        no_match = _NO_PATTERN.search(resolution_text)
        if no_match:
            no_terms = _significant_terms(no_match.group(1))
        auth_match = re.search(r"(?:as (?:reported|determined|confirmed) by|per|according to)\s+([A-Z][\w .&]+)", resolution_text)
        if auth_match:
            resolution_authority = auth_match.group(1).strip().rstrip(".")

    if yes_terms:
        yes_condition = "resolves YES if: " + ", ".join(yes_terms)
    elif event_type and direction == "yes_if_occurs":
        yes_condition = f"resolves YES if the '{event_type}' event described by the question actually occurs"
    else:
        yes_condition = "resolves YES under conditions not confidently parsed from the question text"
        ambiguity_flags.append("yes_condition_not_parsed")

    if no_terms:
        no_condition = "resolves NO if: " + ", ".join(no_terms)
    elif event_type and direction == "yes_if_occurs":
        no_condition = f"resolves NO if the '{event_type}' event does not occur (status quo continues)"
    else:
        no_condition = "resolves NO under conditions not confidently parsed from the question text"
        ambiguity_flags.append("no_condition_not_parsed")

    predicate = event_type
    object_ = None

    # asset was declared on MarketProposition (E4) and detect_price_asset()
    # exists precisely to populate it, but nothing ever called it here —
    # quant.py could never receive a real asset id from the live parsing
    # pipeline. Populate it whenever the event_type is a price threshold.
    asset: str | None = None
    if event_type in ("price_above", "price_below"):
        asset = detect_price_asset(primary_text) or detect_price_asset(question)

    proposition_status: Literal["CLEAR", "AMBIGUOUS"] = "CLEAR"
    if subject is None or event_type is None or "yes_condition_not_parsed" in ambiguity_flags:
        proposition_status = "AMBIGUOUS"

    return MarketProposition(
        subject=subject, predicate=predicate, object=object_, event_type=event_type, direction=direction,
        threshold=threshold, unit=unit, location=None, start_time=None, deadline=deadline,
        yes_condition=yes_condition, no_condition=no_condition, resolution_authority=resolution_authority,
        ambiguity_flags=tuple(ambiguity_flags), proposition_status=proposition_status,
        deadline_semantics=deadline_semantics, asset=asset,
    )


# ---------------------------------------------------------------------------
# A2. Structured Event Extraction
# ---------------------------------------------------------------------------

_REPORTED_TERMS = ("report", "reports", "reported", "according to", "sources say", "officials say")
_CONFIRMED_TERMS = ("confirmed", "confirms", "confirmation", "officially")
_ANNOUNCED_TERMS = ("announces", "announced", "announcement")
_SPECULATIVE_TERMS = (
    "could", "may", "might", "considering", "weighing", "reportedly considering", "rumored",
    # K4 fix: "a ceasefire proposal was submitted" / "expected ceasefire" are
    # not-yet-occurred events that were previously falling through to
    # certainty="unknown" and then status="actual" (see _STATUS_BY_ACTION),
    # wrongly reading identically to a confirmed ceasefire.
    "proposal", "proposed", "submitted", "expected",
)

# Negation/failure terms — a headline can contain an event-family keyword
# (e.g. "ceasefire") while actually reporting that the event did NOT
# happen or fell through ("Ceasefire denied, talks collapse"). Without this
# check the bare keyword match alone would (wrongly) read as the event
# having occurred. This is a real-word/phrase check, not sentiment scoring.
_NEGATION_TERMS = (
    "denied", "denies", "rejected", "rejects", "fails", "failed", "failure", "collapse", "collapses",
    "collapsed", "falls apart", "fell apart", "breaks down", "broke down", "postponed", "delayed",
    "cancelled", "canceled", "ruled out", "backs down", "backed down", "not confirmed",
    # K4 fix: explicit "not <verb>" / modal-negation phrasings were falling
    # through to the plain keyword match (e.g. "Ceasefire not agreed after
    # talks" and "Trump will not resign, aide says" were both wrongly
    # classified as the event having occurred, because "not agreed"/
    # "will not resign" contain no term from the original list above).
    "not agreed", "no agreement", "not reached", "no deal", "will not", "won't", "did not", "does not",
    # K4 fix: "Ceasefire expires at midnight" was matching the bare keyword
    # "ceasefire" (in _DEESCALATION_TERMS) and being read as a NEW ceasefire
    # taking effect, when it actually reports an EXISTING one ending — the
    # opposite direction. Scoped narrowly (only fires when combined with an
    # already-detected escalation/deescalation action, same as every other
    # entry in this tuple) so it doesn't affect unrelated "expires" usages.
    "expires", "expiring", "expired",
)

# event_types that describe opposite outcomes of the same underlying
# situation (e.g. a conflict either escalates or de-escalates). Declared
# here (used by both extract_event's negation handling and
# classify_evidence_relation's topic gate).
#
# E9: renamed from the old placeholder "conflict_escalation" /
# "conflict_deescalation" strings to "war_escalation" / "ceasefire" — the
# exact event_type vocabulary geopolitics.py's _EVENT_TYPES actually
# checks for. The old strings were never in specialized_router.py's
# _EVENT_TYPE_TO_MODEL mapping at all, so nothing downstream depended on
# them; this closes the wiring gap instead of just relocating it.
_OPPOSITE_EVENT_TYPES: dict[str, str] = {
    "war_escalation": "ceasefire",
    "ceasefire": "war_escalation",
}

# Ordered so that more-specific / status-distinguishing phrasings (a call
# for resignation, an announced future intent) are checked BEFORE the
# generic "resign" family — otherwise "Senator calls on Trump to resign"
# would match on the bare substring "resign" and be misread as an actual
# resignation instead of a demand for one.
_ACTION_FAMILIES: dict[str, tuple[str, ...]] = {
    "call_for_resignation": _CALL_FOR_TERMS,
    "announce_intent_to_resign": _ANNOUNCE_INTENT_TERMS,
    "resignation": _RESIGNATION_TERMS,
    "official_duty": _OFFICIAL_DUTY_TERMS,
    "routine_activity": _ROUTINE_ACTIVITY_TERMS,
    "escalation": _ESCALATION_TERMS,
    "deescalation": _DEESCALATION_TERMS,
}


def _extract_actors(text: str) -> tuple[str, ...]:
    # Naive proper-noun run extraction — sequences of capitalized words,
    # excluding common sentence-initial capitalization false positives.
    candidates = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", text)
    seen: set[str] = set()
    actors: list[str] = []
    for c in candidates:
        if c in ("The", "A", "An", "Will", "This") or len(c) < 3:
            continue
        if c not in seen:
            seen.add(c)
            actors.append(c)
    return tuple(actors[:6])


def _detect_action(text: str) -> tuple[str | None, str | None]:
    """Returns (action_family, matched_phrase)."""
    lowered = text.lower()
    for family, terms in _ACTION_FAMILIES.items():
        for term in terms:
            if term in lowered:
                return family, term
    return None, None


def _detect_certainty(text: str) -> Literal["confirmed", "reported", "announced", "speculative", "unknown"]:
    lowered = text.lower()
    if any(t in lowered for t in _CONFIRMED_TERMS):
        return "confirmed"
    if any(t in lowered for t in _SPECULATIVE_TERMS):
        return "speculative"
    if any(t in lowered for t in _ANNOUNCED_TERMS):
        return "announced"
    if any(t in lowered for t in _REPORTED_TERMS):
        return "reported"
    return "unknown"


@dataclass(frozen=True)
class ExtractedEvent:
    actors: tuple[str, ...]
    action: str | None  # action-family label, e.g. "resignation", "call_for_resignation"
    target: str | None
    event_type: str | None
    location: str | None
    event_time: str | None
    expected_time: str | None
    status: Literal["actual", "intent", "call_for", "continuation", "unknown"]
    source: str | None
    source_type: str | None
    certainty: Literal["confirmed", "reported", "announced", "speculative", "unknown"]

    def as_dict(self) -> dict:
        return {
            "actors": list(self.actors), "action": self.action, "target": self.target,
            "event_type": self.event_type, "location": self.location, "event_time": self.event_time,
            "expected_time": self.expected_time, "status": self.status, "source": self.source,
            "source_type": self.source_type, "certainty": self.certainty,
        }


_STATUS_BY_ACTION: dict[str, Literal["actual", "intent", "call_for", "continuation", "unknown"]] = {
    "resignation": "actual",
    "call_for_resignation": "call_for",
    "announce_intent_to_resign": "intent",
    "official_duty": "continuation",
    "routine_activity": "unknown",
    "escalation": "actual",
    "deescalation": "actual",
}


def extract_event(title: str, body: str | None = None) -> ExtractedEvent:
    """Rule-based ("NER-lite") extraction. Deliberately conservative: if
    actors/action can't be extracted with reasonable confidence, the fields
    are left empty/None rather than guessed."""
    text = title if not body else f"{title}. {body}"

    actors = _extract_actors(title)
    action, matched_phrase = _detect_action(text)
    certainty = _detect_certainty(text)

    status: Literal["actual", "intent", "call_for", "continuation", "unknown"] = _STATUS_BY_ACTION.get(action, "unknown") if action else "unknown"

    # K4 fix: escalation/deescalation status was hardcoded to "actual"
    # regardless of certainty (see _STATUS_BY_ACTION), so a merely proposed/
    # expected/speculative ceasefire ("Ceasefire proposal submitted",
    # "Ceasefire expected next week") was read as confidently as a confirmed
    # one. Downgrade to "intent" (not-yet-occurred) when the detected
    # certainty is speculative — mirrors how office_departure already
    # distinguishes "intent" from "actual".
    if action in ("escalation", "deescalation") and certainty == "speculative":
        status = "intent"

    event_type = None
    if action in ("resignation", "announce_intent_to_resign", "call_for_resignation", "official_duty", "routine_activity"):
        # official_duty/routine_activity are only meaningful *relative to*
        # an office_departure proposition (they speak to whether the
        # subject is still active in the role) — classify_evidence_relation
        # is what decides whether that's actually informative or just
        # off-predicate context.
        event_type = "office_departure"
    elif action == "escalation":
        event_type = "war_escalation"
    elif action == "deescalation":
        event_type = "ceasefire"

    # Negation handling: "Ceasefire denied, talks collapse" contains the
    # keyword "ceasefire" but reports the OPPOSITE of a ceasefire actually
    # happening. For conflict event types, swap to the paired opposite type
    # (the failure of de-escalation is itself weak evidence of continued
    # escalation/status-quo). For office-departure actions, a negated
    # resignation/announcement means the subject is still in place —
    # equivalent to a "continuation" status.
    lowered = text.lower()
    if action is not None and any(t in lowered for t in _NEGATION_TERMS):
        if event_type in _OPPOSITE_EVENT_TYPES:
            event_type = _OPPOSITE_EVENT_TYPES[event_type]
            status = "actual"
        elif action in ("resignation", "announce_intent_to_resign"):
            status = "continuation"

    return ExtractedEvent(
        actors=actors, action=action, target=matched_phrase, event_type=event_type, location=None,
        event_time=None, expected_time=None, status=status, source=None, source_type=None, certainty=certainty,
    )


# ---------------------------------------------------------------------------
# A3. Evidence Relation Classification
# ---------------------------------------------------------------------------

# Minimum sentiment magnitude to even consider the weak/tone-based tier.
_WEAK_SENTIMENT_THRESHOLD = 0.2


@dataclass(frozen=True)
class EvidenceRelation:
    label: EvidenceRelationLabel
    entailment: EntailmentTag
    quantitative_weight: float
    detail: str

    def as_dict(self) -> dict:
        return {
            "label": self.label, "entailment": self.entailment,
            "quantitative_weight": self.quantitative_weight, "detail": self.detail,
        }


def _actor_overlaps_subject(event: ExtractedEvent, proposition: MarketProposition) -> bool:
    if not proposition.subject:
        return False
    subject_lower = proposition.subject.lower()
    subject_terms = set(subject_lower.split())
    for actor in event.actors:
        actor_terms = set(actor.lower().split())
        if actor_terms & subject_terms:
            return True
    return False


def _relation_kind(proposition: MarketProposition, event: ExtractedEvent) -> Literal["same", "opposite", "none"]:
    if proposition.event_type is None or event.event_type is None:
        return "none"
    if proposition.event_type == event.event_type:
        return "same"
    if _OPPOSITE_EVENT_TYPES.get(proposition.event_type) == event.event_type:
        return "opposite"
    return "none"


def classify_evidence_relation(
    proposition: MarketProposition,
    event: ExtractedEvent,
    sentiment: float,
    link_confidence: float,
) -> EvidenceRelation:
    """Does this specific extracted event change the probability of this
    proposition's YES condition? This is the entailment question, not a
    tone question. Sentiment is only ever consulted as a last-resort, weak
    signal — and only when the event is already topically on-predicate
    (subject/topic overlap AND a recognized action family related to the
    proposition's event_type), never from a bare actor-name match."""
    actor_overlap = _actor_overlaps_subject(event, proposition)
    relation_kind = _relation_kind(proposition, event)

    # Topic gate: for propositions with a named subject (e.g. "Trump"),
    # the event must mention that subject. For subject-less propositions
    # (e.g. "Will war escalate?"), fall back to event-type domain overlap
    # (same or opposite event family) since there is no named entity to
    # match against.
    if proposition.subject is not None:
        topic_ok = actor_overlap
    else:
        topic_ok = relation_kind != "none"

    if not topic_ok:
        return EvidenceRelation(
            "IRRELEVANT", "NEUTRAL", 0.0,
            "Kein Themen-/Akteur-Überlapp mit dem Subjekt der Marktfrage — Sentiment allein zaehlt nicht als Evidenz.",
        )

    if proposition.proposition_status == "AMBIGUOUS" and proposition.event_type is None:
        return EvidenceRelation(
            "AMBIGUOUS", "NEUTRAL", 0.0,
            "Marktaussage konnte nicht eindeutig geparst werden — keine belastbare Einordnung moeglich.",
        )

    # Routine appearances (rallies, campaign stops) are on-topic entity but
    # not informative about the proposition either direction, regardless of
    # event-family bookkeeping.
    if event.action == "routine_activity":
        return EvidenceRelation(
            "CONTEXT", "NEUTRAL", 0.0,
            "Routinemaessiger Auftritt (z. B. Rally) — kein informativer Bezug zur YES/NO-Bedingung.",
        )

    # Actor/topic overlaps but action is unrecognized or off-predicate
    # (e.g. a jobs announcement) — on-topic entity, off-topic action.
    if event.action is None or relation_kind == "none":
        # "official_duty" (presidential event, press briefing, executive
        # order) is a special case: it implies the subject is still
        # actively in their role, which is weak evidence AGAINST an
        # "office_departure" proposition resolving YES.
        if event.action == "official_duty" and proposition.event_type == "office_departure":
            return EvidenceRelation(
                "SUPPORTS_NO", "CONTRADICTS", 0.35,
                "Ereignis impliziert fortlaufende Amtsausuebung (z. B. offizieller Termin) — spricht gegen "
                "das Eintreten der YES-Bedingung.",
            )
        return EvidenceRelation(
            "CONTEXT", "NEUTRAL", 0.0,
            "Akteur/Thema stimmt ueberein, aber die beschriebene Handlung betrifft nicht das Praedikat der Marktfrage.",
        )

    # From here: on-topic + event family is either the SAME as the
    # proposition's or its recognized OPPOSITE (e.g. de-escalation news
    # against an escalation proposition).
    strong_certainty = event.certainty in ("confirmed", "reported", "announced")
    if proposition.direction == "yes_if_occurs":
        if relation_kind == "same":
            if event.status == "actual" and strong_certainty:
                return EvidenceRelation(
                    "DIRECT_YES", "ENTAILS", 1.0,
                    f"Ereignis ('{event.action}', Status='{event.status}', Certainty='{event.certainty}') "
                    "entspricht direkt der YES-Bedingung der Marktfrage.",
                )
            if event.status == "actual":
                return EvidenceRelation(
                    "SUPPORTS_YES", "ENTAILS", 0.55,
                    "Ereignis vom passenden Typ tatsaechlich eingetreten, aber Sicherheit/Quellenlage noch "
                    "unklar — unterstuetzt YES, ist aber kein bestaetigtes Direktsignal.",
                )
            if event.status == "intent" and strong_certainty:
                return EvidenceRelation(
                    "DIRECT_YES", "ENTAILS", 0.85,
                    "Konkrete, angekuendigte Absicht mit Wirkungsdatum — starkes, aber noch nicht vollzogenes "
                    "YES-Signal.",
                )
            if event.status == "call_for":
                # A demand/call for the event is not the event itself.
                return EvidenceRelation(
                    "WEAK_YES", "ENTAILS", 0.15,
                    "Forderung/Aufruf zum Ereignis (z. B. Ruecktrittsforderung) ist kein tatsaechliches "
                    "Eintreten der YES-Bedingung — nur schwaches Signal.",
                )
            if event.status == "continuation":
                return EvidenceRelation(
                    "SUPPORTS_NO", "CONTRADICTS", 0.35,
                    "Ereignis impliziert Fortsetzung des Status quo, nicht das Eintreten der YES-Bedingung.",
                )
        elif relation_kind == "opposite":
            if event.status == "actual" and strong_certainty:
                return EvidenceRelation(
                    "DIRECT_NO", "CONTRADICTS", 1.0,
                    f"Ereignis vom entgegengesetzten Typ ('{event.action}') bestaetigt eingetreten — "
                    "widerspricht direkt der YES-Bedingung.",
                )
            if event.status == "actual":
                return EvidenceRelation(
                    "SUPPORTS_NO", "CONTRADICTS", 0.55,
                    "Ereignis vom entgegengesetzten Typ eingetreten, Sicherheit noch unklar — spricht gegen "
                    "die YES-Bedingung.",
                )

    # Fallback within on-topic same/opposite family but unresolved status
    # combination — last-resort, tone-gated weak signal, only reachable
    # because we already confirmed topic overlap AND on-predicate action
    # family above.
    if link_confidence >= 0.35 and abs(sentiment) >= _WEAK_SENTIMENT_THRESHOLD:
        sentiment_implies_yes = sentiment > 0 if relation_kind == "same" else sentiment < 0
        if sentiment_implies_yes:
            return EvidenceRelation(
                "WEAK_YES" if proposition.direction == "yes_if_occurs" else "WEAK_NO",
                "ENTAILS", 0.1, "Nur schwaches, tonalitaetsbasiertes Signal innerhalb desselben Themenbereichs.",
            )
        return EvidenceRelation(
            "WEAK_NO" if proposition.direction == "yes_if_occurs" else "WEAK_YES",
            "CONTRADICTS", 0.1, "Nur schwaches, tonalitaetsbasiertes Signal innerhalb desselben Themenbereichs.",
        )

    return EvidenceRelation(
        "AMBIGUOUS", "NEUTRAL", 0.0,
        "Passender Themenbereich, aber Status/Sicherheit nicht eindeutig genug fuer eine gerichtete Einordnung.",
    )
