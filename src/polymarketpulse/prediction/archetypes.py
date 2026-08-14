"""Forecast-archetype registry.

The registry is deliberately small and deterministic.  It answers a product
question before any numeric code is allowed to run: does this market have a
model family whose target and inputs are actually defined?  Generic research
signals are never an archetype and therefore never grant a numeric forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .semantics import MarketProposition

ArchetypeName = Literal[
    "MACRO_POLICY", "LEGISLATIVE_PROCESS", "GEOPOLITICAL_STATE_TRANSITION",
    "PRICE_THRESHOLD", "CRYPTO_REGULATORY", "SPORTS_EVENT", "GENERIC_RESEARCH_ONLY",
]
CapabilityState = Literal[
    "UNSUPPORTED", "DATASET_BUILDING", "SHADOW_READY", "SHADOW_VALIDATED", "PUBLISH_ELIGIBLE",
]


@dataclass(frozen=True)
class ForecastArchetype:
    name: ArchetypeName
    capability_state: CapabilityState
    required_semantics: tuple[str, ...]
    allowed_input_types: tuple[str, ...]
    failure_reasons: tuple[str, ...]


REGISTRY: dict[ArchetypeName, ForecastArchetype] = {
    "MACRO_POLICY": ForecastArchetype(
        "MACRO_POLICY", "SHADOW_VALIDATED",
        ("central_bank", "exact_outcome_bucket", "meeting_or_deadline"),
        ("official_policy_history", "official_macro_snapshot"),
        ("SEMANTICS_UNCERTAIN", "CRITICAL_INPUT_MISSING", "CRITICAL_INPUT_STALE", "MODEL_NOT_VALIDATED"),
    ),
    "LEGISLATIVE_PROCESS": ForecastArchetype(
        "LEGISLATIVE_PROCESS", "DATASET_BUILDING",
        ("identified_bill", "official_stage", "deadline"),
        ("official_legislative_stage", "historical_transition_dataset"),
        ("SEMANTICS_UNCERTAIN", "CRITICAL_INPUT_MISSING", "MODEL_NOT_VALIDATED"),
    ),
    "GEOPOLITICAL_STATE_TRANSITION": ForecastArchetype(
        "GEOPOLITICAL_STATE_TRANSITION", "DATASET_BUILDING",
        ("structured_measurement", "threshold_or_state", "deadline"),
        ("structured_observation", "historical_transition_dataset"),
        ("SEMANTICS_UNCERTAIN", "CRITICAL_INPUT_MISSING", "MODEL_NOT_VALIDATED"),
    ),
    "PRICE_THRESHOLD": ForecastArchetype("PRICE_THRESHOLD", "DATASET_BUILDING", ("asset", "threshold", "deadline"), ("price_history",), ("MODEL_NOT_VALIDATED",)),
    "CRYPTO_REGULATORY": ForecastArchetype("CRYPTO_REGULATORY", "UNSUPPORTED", ("identified_rule", "deadline"), (), ("NO_ARCHETYPE",)),
    "SPORTS_EVENT": ForecastArchetype("SPORTS_EVENT", "UNSUPPORTED", ("event",), (), ("NO_ARCHETYPE",)),
    "GENERIC_RESEARCH_ONLY": ForecastArchetype("GENERIC_RESEARCH_ONLY", "UNSUPPORTED", (), (), ("NO_ARCHETYPE",)),
}


def route_archetype(
    proposition: MarketProposition | None, question: str, resolution_text: str | None,
    category: str | None,
) -> ForecastArchetype:
    """Route only from validated semantics/category, never an LLM guess."""
    text = f"{question} {resolution_text or ''}".lower()
    event_type = proposition.event_type if proposition is not None else None
    if (
        (category == "CENTRAL_BANKS" or "fomc" in text or "federal reserve" in text)
        and event_type in {"rate_cut", "rate_hike", "rate_hold", "central_bank_decision"}
    ):
        return REGISTRY["MACRO_POLICY"]
    if event_type == "legislation":
        return REGISTRY["LEGISLATIVE_PROCESS"]
    if event_type == "strategic_waterway":
        return REGISTRY["GEOPOLITICAL_STATE_TRANSITION"]
    if event_type in {"price_above", "price_below"}:
        return REGISTRY["PRICE_THRESHOLD"]
    if event_type and event_type.startswith("sport_"):
        return REGISTRY["SPORTS_EVENT"]
    return REGISTRY["GENERIC_RESEARCH_ONLY"]
