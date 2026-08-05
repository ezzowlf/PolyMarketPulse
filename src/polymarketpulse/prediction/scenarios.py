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

from .news import NewsEvidenceItem
from .types import ScenarioSet, SubmodelEstimate


def build_scenarios(
    estimated_yes_probability: float | None,
    submodel_estimates: list[SubmodelEstimate],
    news_evidence: list[NewsEvidenceItem],
    comparable_sample_size: int,
    recommendation: str,
) -> ScenarioSet:
    if estimated_yes_probability is None:
        base = (
            "Keine belastbare Basisprognose vorhanden — zu wenige historische Vergleichsfälle und/oder kein "
            "aktueller Marktpreis. Weder Bull- noch Bear-Case lassen sich derzeit sinnvoll ableiten."
        )
        return ScenarioSet(base_case=base, bull_case=[], bear_case=[])

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

    return ScenarioSet(base_case=base, bull_case=bull, bear_case=bear)
