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

_OUTCOME_LABEL_DE = {
    "CUT_50_PLUS": "eine Zinssenkung um 50+ Basispunkte",
    "CUT_25": "eine Zinssenkung um 25 Basispunkte",
    "UNCHANGED": "keine Zinsänderung",
    "HIKE_25": "eine Zinserhöhung um 25 Basispunkte",
    "HIKE_50_PLUS": "eine Zinserhöhung um 50+ Basispunkte",
}
_ACTION_LABEL_DE = {
    "CUT_50_PLUS": "einer Zinssenkung um 50+ Basispunkte",
    "CUT_25": "einer Zinssenkung um 25 Basispunkte",
    "UNCHANGED": "unveränderten Zinsen",
    "HIKE_25": "einer Zinserhöhung um 25 Basispunkte",
    "HIKE_50_PLUS": "einer Zinserhöhung um 50+ Basispunkte",
}


def _fed_why_and_summary(prediction, diagnostics: dict, product_probability: float) -> tuple[str, str]:
    """Deterministic, real-number Kernaussage/Warum for the Fed champion
    model -- built only from diagnostics.py's already-computed real fields
    (target outcome, live prior action, real observed transition counts,
    real validation metrics). No free text, no LLM."""
    target = diagnostics.get("target") or {}
    outcome_label = _OUTCOME_LABEL_DE.get(target.get("outcome"), target.get("outcome") or "dieses Ergebnis")
    prior_action = diagnostics.get("prior_action")
    prior_label = _ACTION_LABEL_DE.get(prior_action, prior_action or "dem zuletzt bekannten Fed-Beschluss")
    basis = diagnostics.get("transition_basis") or {}
    observed = basis.get("observed_target_count")
    total = basis.get("observed_total_count")
    market_p = getattr(prediction, "market_yes_probability", None)
    diff_pp = round((product_probability - market_p) * 100, 1) if market_p is not None else None

    why_parts = [
        (
            f"Historisches Fed-Übergangsmodell: nach {prior_label} folgte {outcome_label} bei "
            f"{observed} von {total} vergleichbaren FOMC-Meetings (2021-2025, mit Laplace-Glättung)."
        )
    ]
    if diff_pp is not None:
        direction = "niedriger" if diff_pp < 0 else "höher" if diff_pp > 0 else "gleich"
        why_parts.append(f"Das Modell liegt damit {abs(diff_pp):.1f} Prozentpunkte {direction} als der aktuelle Marktpreis.")
    why_numeric = " ".join(why_parts)

    summary = (
        f"Das validierte Fed-Modell bewertet {outcome_label} nach {prior_label} auf Basis von "
        f"{total} historischen FOMC-Übergängen deutlich anders als der Markt."
        if diff_pp is not None and abs(diff_pp) >= 5
        else "Das validierte Fed-Modell liegt aktuell nahe am Markt; ein großer unabhängiger Vorteil ist nicht erkennbar."
    )
    return why_numeric, summary


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
        product_probability = prediction.model_hypothesis_probability
        market_p = getattr(prediction, "market_yes_probability", None)
        diff_pp = round((product_probability - market_p) * 100, 1) if market_p is not None else None
        why_numeric, summary = _fed_why_and_summary(prediction, diagnostics, product_probability)
        target = diagnostics.get("target") or {}
        return {
            "product_mode": "VALIDATED_NUMERIC_FORECAST",
            "product_probability": product_probability,
            "differenz_pp": diff_pp,
            "model_lifecycle": "CHAMPION",
            "summary": summary,
            "why_numeric": why_numeric,
            # Deliberately NOT "next_event" -- PredictionResult.as_dict()
            # already uses that key for the full NextEvent object (Phase F);
            # overwriting it with a plain string here would silently drop
            # the structured object for every other consumer.
            "next_macro_event": f"Nächstes FOMC-Meeting: {target.get('meeting_date')}" if target.get("meeting_date") else None,
            "change_drivers": [
                "eine neue offizielle FOMC-Entscheidung vor dem Zielmeeting (aktualisiert den Prior-State)",
                "ein aktualisierter Trainingsdatensatz mit weiteren realen FOMC-Übergängen",
            ],
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
    gap_list = list(getattr(gaps, "gaps", ()))[:3]
    missing = [gap.description for gap in gap_list]
    reason = prediction.numeric_model_reason_code
    if reason:
        missing.insert(0, f"Modell-Input nicht verfügbar: {reason}")
    # Phase 7.1: the same top gaps, but machine-actionable -- real gap_type
    # (see data_gaps.py's GapType taxonomy) and the real recommended
    # sources already computed by the Data Gap Engine's source-registry
    # routing, so a future research step can act on WHICH provider to try
    # next rather than re-parsing free text. Additive: `missing` (plain
    # strings, already consumed by the UI) is unchanged.
    data_gaps_detail = [
        {
            "gap_type": gap.gap_type,
            "description": gap.description,
            "severity": gap.severity,
            "recommended_sources": list(gap.recommended_sources),
        }
        for gap in gap_list
    ]
    if reason:
        data_gaps_detail.insert(0, {
            "gap_type": "NO_ARCHETYPE" if reason == "NO_ARCHETYPE" else "MISSING_MODEL_INPUT",
            "description": f"Modell-Input nicht verfügbar: {reason}",
            "severity": "HIGH",
            "recommended_sources": [],
        })
    return {
        "product_mode": "INSUFFICIENT_DATA",
        "product_probability": None,
        "model_lifecycle": None,
        "summary": "Noch fehlen belastbare, marktbezogene Informationen für eine strukturierte Einschätzung.",
        "why_numeric": None,
        "missing": missing,
        "data_gaps_detail": data_gaps_detail,
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
