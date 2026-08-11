"""Scenario Engine — builds Base/Bull/Bear case descriptions purely from
already-computed structured facts (submodel estimates, news evidence,
deadline phase). No LLM is involved in deciding what the scenarios *say*;
that would violate the core rule that the model never invents probability-
relevant content. GPT (in the explanation layer) is only ever handed this
finished, structured `ScenarioSet` to phrase more fluently — see
ai/prompts.py rule 8 ("explain clearly whether the analysis points to YES,
NO or NO_BET").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .news import NewsEvidenceItem
from .types import Scenario, ScenarioSet, SubmodelEstimate

if TYPE_CHECKING:
    from .evidence import EvidenceFactor
    from .resolution_semantics import ResolutionSemantics
    from .world_state import ResolutionPath

_STEP_NAME_DE: dict[str, str] = {
    "introduced": "Einbringung des Gesetzentwurfs",
    "committee": "Abschluss der Ausschussphase",
    "house_vote": "Abstimmung im Repräsentantenhaus",
    "senate_vote": "Abstimmung im Senat",
    "presidential_action": "Unterzeichnung oder Veto durch den Präsidenten",
}


def _build_resolution_path_scenarios(
    resolution_path: ResolutionPath,
    evidence_for_yes: tuple[EvidenceFactor, ...],
    evidence_for_no: tuple[EvidenceFactor, ...],
    change_triggers: tuple[str, ...],
) -> tuple[Scenario, ...]:
    """Block F Part 1: rich scenarios for markets with a real, structured
    multi-step ResolutionPath (Block C — e.g. legislation markets). Every
    step name/status comes directly from `resolution_path.steps`; nothing
    here is guessed. `probability` stays None — no per-scenario probability
    formula exists anywhere in this codebase, so attaching one would be
    fabrication."""
    done_steps = [s for s in resolution_path.steps if s.status == "done"]
    open_steps = [s for s in resolution_path.steps if s.status in ("unknown", "blocked")]
    step_labels = [_STEP_NAME_DE.get(s.name, s.name) for s in resolution_path.steps]

    supporting = tuple(e.title for e in evidence_for_yes[:5])
    contradicting = tuple(e.title for e in evidence_for_no[:5])

    yes_necessary = tuple(_STEP_NAME_DE.get(s.name, s.name) for s in open_steps)
    yes_desc = (
        "YES-Szenario: " + " → ".join(step_labels) + " vor der Deadline abgeschlossen. "
        f"Bereits abgeschlossen: {len(done_steps)}/{len(resolution_path.steps)} Schritte."
    )
    yes_scenario = Scenario(
        outcome="YES",
        description=yes_desc,
        necessary_events=yes_necessary,
        supporting_claims=supporting,
        contradicting_claims=contradicting,
        triggers=change_triggers,
        probability=None,
    )

    blocked_steps = [s for s in resolution_path.steps if s.status == "blocked"]
    no_desc = (
        "NO-Szenario: mindestens ein notwendiger Schritt ("
        + ", ".join(_STEP_NAME_DE.get(s.name, s.name) for s in (blocked_steps or open_steps or resolution_path.steps))
        + ") wird nicht rechtzeitig abgeschlossen oder scheitert."
    )
    no_scenario = Scenario(
        outcome="NO",
        description=no_desc,
        necessary_events=tuple(_STEP_NAME_DE.get(s.name, s.name) for s in (blocked_steps or open_steps)),
        supporting_claims=contradicting,
        contradicting_claims=supporting,
        triggers=change_triggers,
        probability=None,
    )
    return (yes_scenario, no_scenario)


def _build_binary_scenarios(
    resolution_semantics: ResolutionSemantics,
    evidence_for_yes: tuple[EvidenceFactor, ...],
    evidence_for_no: tuple[EvidenceFactor, ...],
    change_triggers: tuple[str, ...],
) -> tuple[Scenario, ...]:
    """Block F Part 1: minimal, honest scenario pair for simple binary
    markets with no real multi-step ResolutionPath — just the real
    yes_condition/no_condition text (resolution_semantics.py) plus whatever
    real supporting/contradicting evidence exists. No fabricated richness
    for markets with no real underlying structure to support it."""
    supporting = tuple(e.title for e in evidence_for_yes[:5])
    contradicting = tuple(e.title for e in evidence_for_no[:5])
    yes_scenario = Scenario(
        outcome="YES",
        description=f"YES: {resolution_semantics.yes_condition}",
        supporting_claims=supporting,
        contradicting_claims=contradicting,
        triggers=change_triggers,
        probability=None,
    )
    no_scenario = Scenario(
        outcome="NO",
        description=f"NO: {resolution_semantics.no_condition}",
        supporting_claims=contradicting,
        contradicting_claims=supporting,
        triggers=change_triggers,
        probability=None,
    )
    return (yes_scenario, no_scenario)


def build_scenarios(
    estimated_yes_probability: float | None,
    submodel_estimates: list[SubmodelEstimate],
    news_evidence: list[NewsEvidenceItem],
    comparable_sample_size: int,
    recommendation: str,
    *,
    resolution_path: ResolutionPath | None = None,
    resolution_semantics: ResolutionSemantics | None = None,
    evidence_for_yes: tuple[EvidenceFactor, ...] = (),
    evidence_for_no: tuple[EvidenceFactor, ...] = (),
    change_triggers: tuple[str, ...] = (),
) -> ScenarioSet:
    # Block F Part 1: genuinely-derived scenarios, independent of whether an
    # ensemble estimate exists — a market can have a real ResolutionPath (or
    # at minimum real yes/no condition text) even when the probabilistic
    # engine honestly declines to produce a number. Richer structure wins
    # over the minimal binary form whenever a real multi-step path exists.
    if resolution_path is not None and resolution_path.applies and resolution_path.steps:
        derived_scenarios = _build_resolution_path_scenarios(
            resolution_path, evidence_for_yes, evidence_for_no, change_triggers
        )
    elif resolution_semantics is not None and resolution_semantics.yes_condition and resolution_semantics.no_condition:
        derived_scenarios = _build_binary_scenarios(
            resolution_semantics, evidence_for_yes, evidence_for_no, change_triggers
        )
    else:
        derived_scenarios = ()

    if estimated_yes_probability is None:
        base = (
            "Keine belastbare Basisprognose vorhanden — zu wenige historische Vergleichsfälle und/oder kein "
            "aktueller Marktpreis. Weder Bull- noch Bear-Case lassen sich derzeit sinnvoll ableiten."
        )
        return ScenarioSet(base_case=base, bull_case=[], bear_case=[], scenarios=derived_scenarios)

    base = (
        f"Wahrscheinlichster Verlauf laut Ensemble: YES-Wahrscheinlichkeit ~{estimated_yes_probability:.0%}, "
        f"gestützt auf {len([s for s in submodel_estimates if s.available])} verfügbare(n) Teilmodell(e) "
        f"und {comparable_sample_size} historische Vergleichsfälle. Empfehlung: {recommendation}."
    )

    bull: list[str] = []
    bear: list[str] = []

    history = next((s for s in submodel_estimates if s.name == "history"), None)
    if history and history.available and history.estimated_yes_probability is not None:
        if history.estimated_yes_probability > (estimated_yes_probability or 0):
            bull.append(
                f"Historische Basisrate ({history.estimated_yes_probability:.0%}) liegt über der aktuellen "
                "Ensemble-Schätzung — weitere vergleichbare Fälle könnten die Prognose weiter Richtung YES ziehen."
            )
        elif history.estimated_yes_probability < (estimated_yes_probability or 0):
            bear.append(
                f"Historische Basisrate ({history.estimated_yes_probability:.0%}) liegt unter der aktuellen "
                "Ensemble-Schätzung — ein Rückfall auf das historische Muster würde Richtung NO wirken."
            )

    momentum = next((s for s in submodel_estimates if s.name == "momentum"), None)
    if momentum and momentum.available:
        bull.append(f"Anhaltendes Preis-Momentum in dieselbe Richtung würde die YES-Schätzung stützen. ({momentum.detail})")
        bear.append(f"Eine Trendumkehr oder Mean-Reversion würde die Schätzung Richtung NO drücken. ({momentum.detail})")

    positive_news = [e for e in news_evidence if e.sentiment > 0.1]
    negative_news = [e for e in news_evidence if e.sentiment < -0.1]
    for e in positive_news[:3]:
        bull.append(f"Positive Nachricht ({e.source}): \"{e.title}\" könnte die YES-Wahrscheinlichkeit weiter erhöhen, falls bestätigt.")
    for e in negative_news[:3]:
        bear.append(f"Negative Nachricht ({e.source}): \"{e.title}\" könnte die YES-Wahrscheinlichkeit weiter senken, falls bestätigt.")

    if not bull:
        bull.append("Keine spezifischen Bull-Faktoren aus den verfügbaren Teilmodellen identifiziert.")
    if not bear:
        bear.append("Keine spezifischen Bear-Faktoren aus den verfügbaren Teilmodellen identifiziert.")

    return ScenarioSet(base_case=base, bull_case=bull, bear_case=bear, scenarios=derived_scenarios)
