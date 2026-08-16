"""Small, presentation-only product contract.

This module never creates a probability.  It translates already-computed
forecast and structured-state facts into one mutually exclusive user-facing
mode so normal surfaces do not expose the engine's legacy status taxonomy.
"""

from __future__ import annotations

from typing import Literal

ProductMode = Literal[
    "VALIDATED_NUMERIC_FORECAST", "STRUCTURED_OUTLOOK", "INSUFFICIENT_DATA", "UNSUPPORTED"
]


def product_mode_for_prediction(prediction) -> dict:
    diagnostics = prediction.model_diagnostics or {}
    validation = diagnostics.get("validation") or {}
    valid_fed_model = (
        prediction.forecast_archetype == "MACRO_POLICY"
        and prediction.model_hypothesis_probability is not None
        and prediction.numeric_model_reason_code is None
        and validation.get("passed") is True
    )
    if valid_fed_model:
        return {
            "product_mode": "VALIDATED_NUMERIC_FORECAST",
            "product_probability": prediction.model_hypothesis_probability,
            "model_lifecycle": "CHAMPION",
            "summary": "Das validierte Fed-Modell liegt aktuell nahe am Markt; ein großer unabhängiger Vorteil ist nicht erkennbar.",
            "why_numeric": "Zeitgetrennt validiertes Fed-Modell mit offizieller Vorentscheidung als einzigem Modell-Input.",
            "missing": [],
            "next_research": None,
        }

    has_structure = any(
        (
            prediction.structured_world_state,
            prediction.next_event,
            prediction.scenario_tree,
            prediction.world_state,
        )
    )
    if has_structure:
        return {
            "product_mode": "STRUCTURED_OUTLOOK",
            "product_probability": None,
            "model_lifecycle": None,
            "summary": "Strukturierte Einschätzung: Zustand, nächste Schritte, Szenarien und offene Risiken sind verfügbar; eine validierte Modellwahrscheinlichkeit nicht.",
            "why_numeric": None,
            "missing": [item.description for item in (getattr(prediction, "data_gaps", None).gaps if getattr(prediction, "data_gaps", None) else ())][:3],
            "next_research": getattr(getattr(prediction, "next_event", None), "next_event_description", None),
        }

    gaps = getattr(prediction, "data_gaps", None)
    missing = [gap.description for gap in getattr(gaps, "gaps", ())][:3]
    reason = prediction.numeric_model_reason_code
    if reason:
        missing.insert(0, f"Modell-Input nicht verfügbar: {reason}")
    return {
        "product_mode": "INSUFFICIENT_DATA",
        "product_probability": None,
        "model_lifecycle": None,
        "summary": "Noch fehlen belastbare, marktbezogene Informationen für eine strukturierte Einschätzung.",
        "why_numeric": None,
        "missing": missing,
        "next_research": getattr(getattr(prediction, "next_event", None), "next_event_description", None),
    }


def product_mode_for_market_record(record: dict) -> str:
    """Fast, storage-only list mode; never triggers source retrieval."""
    question = (record.get("question") or "").lower()
    has_fed_model = record.get("model_hypothesis_probability") is not None and record.get("has_champion_macro_model") and (
        "fed" in question or "fomc" in question
    )
    if has_fed_model:
        return "VALIDATED_NUMERIC_FORECAST"
    if record.get("has_research_run"):
        return "STRUCTURED_OUTLOOK"
    return "INSUFFICIENT_DATA"
