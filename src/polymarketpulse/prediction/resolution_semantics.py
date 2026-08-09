"""Resolution Engine — ROUND-1 (85-section brief, section 4).

Audit finding (documented here, not re-litigated in HANDOFF prose alone):
`resolution_rules.py` (the module an earlier round called the "Resolution-
rule parser") is real but narrow — it only extracts YES/NO trigger-term
lists from an explicit "resolves YES/NO if ..." clause plus a bag of
subject terms from the question. It does not classify WHAT is being
measured, WHAT threshold/source is required, or produce any structured
confidence/ambiguity signal. This module builds on top of it (reuses
`parse_resolution_conditions` for the yes/no trigger-term extraction
instead of duplicating that regex) and adds the richer structure the brief
asks for.

`ResolutionSemantics` is deliberately a thin, honest layer over
`semantics.MarketProposition` (already computed by `parse_market_proposition`
in the same call) plus `resolution_rules.parse_resolution_conditions` — it
adds no new probability-affecting computation, no LLM calls, no network
calls. Every field is None/empty when it genuinely cannot be determined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .resolution_rules import parse_resolution_conditions
from .semantics import parse_market_proposition

if TYPE_CHECKING:
    from .semantics import MarketProposition

Measurement = Literal[
    "spot_price", "official_rate_announcement", "official_vote_count", "binary_event_occurrence",
]

# event_type -> what's actually being measured to resolve the market. A
# direct, literal lookup over semantics.py's already-real event_type
# vocabulary — not a new classifier.
_MEASUREMENT_BY_EVENT_TYPE: dict[str, Measurement] = {
    "price_above": "spot_price",
    "price_below": "spot_price",
    "rate_cut": "official_rate_announcement",
    "rate_hike": "official_rate_announcement",
    "rate_hold": "official_rate_announcement",
    "election": "official_vote_count",
    "office_departure": "binary_event_occurrence",
    "legislation": "binary_event_occurrence",
    "appointment": "binary_event_occurrence",
    "court_outcome": "binary_event_occurrence",
    "sanctions": "binary_event_occurrence",
    "strategic_waterway": "binary_event_occurrence",
    "territorial_control": "binary_event_occurrence",
    "diplomatic_agreement": "binary_event_occurrence",
    "military_action": "binary_event_occurrence",
    "ceasefire": "binary_event_occurrence",
    "war_escalation": "binary_event_occurrence",
    "sport_match": "binary_event_occurrence",
    "sport_tournament": "binary_event_occurrence",
    "sport_winner": "binary_event_occurrence",
    "sport_qualification": "binary_event_occurrence",
}

# event_type -> a *documented, honestly-labelled inference* of the typical
# real-world authority that resolves this kind of market, used ONLY when no
# explicit resolution_authority was found in the text itself. Always paired
# with the "resolution_source_inferred_from_domain" ambiguity flag so a
# reader can tell "the text said this" from "this is our best domain guess".
_TYPICAL_SOURCE_BY_DOMAIN: dict[str, str] = {
    "MACRO": "central bank official statement/press release",
    "GEOPOLITICS": "official government/news-agency confirmation",
    "POLITICS": "official government record or election authority",
    "SPORTS": "official league/tournament result",
    "CRYPTO": "exchange/aggregator spot price feed",
}


@dataclass(frozen=True)
class ResolutionSemantics:
    yes_condition: str
    no_condition: str
    deadline: str | None
    measurement: Measurement | None
    threshold: float | None
    required_source: str | None
    ambiguities: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "yes_condition": self.yes_condition,
            "no_condition": self.no_condition,
            "deadline": self.deadline,
            "measurement": self.measurement,
            "threshold": self.threshold,
            "required_source": self.required_source,
            "ambiguities": list(self.ambiguities),
            "confidence": self.confidence,
        }


def extract_resolution_semantics(
    question: str,
    resolution_text: str | None,
    proposition: MarketProposition | None = None,
) -> ResolutionSemantics:
    """Rule-based extraction of `ResolutionSemantics` from market text.
    `proposition` may be passed in when the caller already computed it
    (engine.py always has one) to avoid re-parsing; otherwise it is
    computed here."""
    if proposition is None:
        proposition = parse_market_proposition(question, resolution_text)

    yes_terms, no_terms, _subject_terms = parse_resolution_conditions(question, resolution_text)

    ambiguities: list[str] = []

    if not resolution_text:
        ambiguities.append("no_resolution_text_supplied")
    elif not yes_terms and not no_terms:
        ambiguities.append("no_yes_no_clause_in_resolution_text")

    if proposition.event_type is None:
        ambiguities.append("no_event_type_detected")

    measurement = _MEASUREMENT_BY_EVENT_TYPE.get(proposition.event_type) if proposition.event_type else None
    if proposition.event_type is not None and measurement is None:
        ambiguities.append("event_type_not_mapped_to_a_measurement")

    if proposition.event_type in ("price_above", "price_below") and proposition.threshold is not None and proposition.unit is None:
        ambiguities.append("threshold_present_but_unit_unclear")

    if proposition.deadline is not None and proposition.deadline_semantics is None:
        ambiguities.append("deadline_semantics_unclear_by_vs_at")

    required_source = proposition.resolution_authority
    if required_source is None:
        if proposition.domain is not None and proposition.domain in _TYPICAL_SOURCE_BY_DOMAIN:
            required_source = _TYPICAL_SOURCE_BY_DOMAIN[proposition.domain]
            ambiguities.append("resolution_source_inferred_from_domain")
        else:
            ambiguities.append("no_resolution_source_identified")

    base_confidence = proposition.semantic_confidence if proposition.semantic_confidence is not None else 0.4
    confidence = round(max(0.05, min(1.0, base_confidence - 0.08 * len(ambiguities))), 2)

    return ResolutionSemantics(
        yes_condition=proposition.yes_condition,
        no_condition=proposition.no_condition,
        deadline=proposition.deadline,
        measurement=measurement,
        threshold=proposition.threshold,
        required_source=required_source,
        ambiguities=tuple(ambiguities),
        confidence=confidence,
    )
