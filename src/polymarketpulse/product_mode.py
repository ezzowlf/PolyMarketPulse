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


def _has_real_structure(prediction) -> bool:
    """Real-content check, not object-presence.  `structured_world_state`/
    `next_event`/`world_state` are non-None dataclass instances on almost
    every successfully-computed prediction (they are unconditionally
    constructed by engine.py), so a bare truthiness check on the object
    itself is always True and effectively disables INSUFFICIENT_DATA.  This
    checks whether any of those objects actually carries a real, non-empty
    fact -- a market with a real resolution path/next event/confirmed fact
    is STRUCTURED_OUTLOOK; a market where every one of those fields is
    honestly empty is not."""
    sws = prediction.structured_world_state
    if sws is not None:
        # world_state.py's PathToResolution.current_state falls back to the
        # literal string "UNKNOWN" when nothing real is known (see
        # world_state.py:852) -- that placeholder must not itself count as
        # real content, or every market with a classified category but no
        # actual evidence would falsely qualify.
        current_state = sws.current_state
        has_real_current_state = bool(current_state) and current_state != "UNKNOWN"
        if (
            has_real_current_state
            or sws.confirmed_facts
            or sws.completed_steps
            or sws.open_steps
            or sws.blockers
            or sws.disputed_facts
        ):
            return True
    next_event = prediction.next_event
    if next_event is not None and next_event.next_event_type is not None:
        return True
    scenario_tree = prediction.scenario_tree
    return scenario_tree is not None and bool(scenario_tree.branches)


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

    if _has_real_structure(prediction):
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
