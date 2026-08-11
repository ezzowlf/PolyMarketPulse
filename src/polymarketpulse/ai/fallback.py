from __future__ import annotations

from ..prediction import PredictionResult
from .schemas import ExplanationFactor, ExplanationResult, ProbabilityExplanation

RECOMMENDATION_TEXT_DE = {
    "STRONG_YES": "klar für YES",
    "YES": "für YES",
    "WATCH_YES": "beobachten (leichter YES-Vorteil)",
    "NO_BET": "keine Wette",
    "WATCH_NO": "beobachten (leichter NO-Vorteil)",
    "NO": "für NO",
    "STRONG_NO": "klar für NO",
    "INSUFFICIENT_DATA": "unzureichende Datenlage",
}


def direction_for(recommendation: str) -> str:
    if recommendation in ("STRONG_YES", "YES", "WATCH_YES"):
        return "YES"
    if recommendation in ("STRONG_NO", "NO", "WATCH_NO"):
        return "NO"
    return "NONE"


def build_fallback_explanation(prediction: PredictionResult) -> ExplanationResult:
    """A complete, German, rule-based explanation that requires no AI call
    at all. Used whenever AI is disabled, the budget is exhausted, the API
    fails, or GPT's output fails validation twice in a row. The dashboard
    must never show an empty state when these statistical numbers exist."""
    direction = direction_for(prediction.recommendation)
    market_pct = round(prediction.market_yes_probability * 100) if prediction.market_yes_probability is not None else None
    model_yes_pct = (
        round(prediction.estimated_yes_probability * 100) if prediction.estimated_yes_probability is not None else None
    )
    model_no_pct = (
        round(prediction.estimated_no_probability * 100) if prediction.estimated_no_probability is not None else None
    )
    edge_pp = round(prediction.net_yes_edge * 100) if prediction.net_yes_edge is not None else None

    if prediction.recommendation == "INSUFFICIENT_DATA":
        summary = (
            f"Es liegen zu wenige belastbare historische Vergleichsfälle vor "
            f"({prediction.comparable_sample_size} gefunden), um eine verlässliche Prognose zu erstellen."
        )
        headline = "Keine belastbare Prognose möglich"
    else:
        summary = (
            f"Eigene Prognose: YES {model_yes_pct}% / NO {model_no_pct}%. "
            f"Marktpreis: YES {market_pct}%. "
            f"Netto-Edge: {edge_pp:+d} Prozentpunkte."
            if model_yes_pct is not None and market_pct is not None and edge_pp is not None
            else "Prognose unvollständig."
        )
        headline = f"Empfehlung: {RECOMMENDATION_TEXT_DE.get(prediction.recommendation, prediction.recommendation)}"

    recommendation_explanation = (
        f"Empfehlung {prediction.recommendation} ({RECOMMENDATION_TEXT_DE.get(prediction.recommendation, '')}), "
        f"weil Modellvertrauen {prediction.confidence_score:.0f}/100 und Datenqualität "
        f"{prediction.data_quality.total:.0f}/100 beträgt, basierend auf "
        f"{prediction.comparable_sample_size} historischen Vergleichsfällen."
    )

    uncertainties = list(prediction.reasoning_notes)
    if prediction.uncertainty_lower is not None and prediction.uncertainty_upper is not None:
        uncertainties.append(
            f"Realistische YES-Spanne liegt zwischen {prediction.uncertainty_lower:.0%} und "
            f"{prediction.uncertainty_upper:.0%}."
        )

    data_gaps = []
    if prediction.comparable_sample_size < 5:
        data_gaps.append("Zu wenige historische Vergleichsfälle für eine robuste Basisrate.")

    supports_yes = []
    supports_no = []
    if edge_pp is not None and edge_pp > 0:
        supports_yes.append(
            ExplanationFactor(
                factor=f"Eigene Prognose liegt {edge_pp} Prozentpunkte über dem Marktpreis",
                impact="high" if abs(edge_pp) >= 15 else "medium",
                source_ids=[],
            )
        )
    elif edge_pp is not None and edge_pp < 0:
        supports_no.append(
            ExplanationFactor(
                factor=f"Eigene Prognose liegt {abs(edge_pp)} Prozentpunkte unter dem Marktpreis",
                impact="high" if abs(edge_pp) >= 15 else "medium",
                source_ids=[],
            )
        )

    # Block F Part 2 (additive): "WAS WISSEN WIR" / "WARUM WEICHEN WIR VOM
    # MARKT AB" free-text fields, built the same rule-based way as the rest
    # of this fallback — real numbers only, no invention.
    what_we_know = (
        f"Publizierbare Prognose: {round(prediction.published_forecast_probability * 100)}% YES "
        f"(Status: {prediction.forecast_status}, Reifegrad: {prediction.forecast_maturity})."
        if prediction.published_forecast_probability is not None
        else f"Keine publizierbare Prognose (Status: {prediction.forecast_status}, "
        f"Reifegrad: {prediction.forecast_maturity})."
    )
    if prediction.independent_probability is not None and market_pct is not None:
        divergence_pp = round(prediction.independent_probability * 100) - market_pct
        divergence_explanation = (
            f"Eigene unabhängige Einschätzung ({round(prediction.independent_probability * 100)}%) weicht "
            f"{divergence_pp:+d} Prozentpunkte vom Marktpreis ({market_pct}%) ab."
        )
        if prediction.divergence_audit is not None:
            divergence_explanation += f" Divergenz-Audit: {prediction.divergence_audit.verdict}."
    else:
        divergence_explanation = "Keine unabhängige Einschätzung verfügbar, daher keine Marktabweichung zu erklären."

    return ExplanationResult(
        direction=direction,
        recommendation=prediction.recommendation,
        headline=headline,
        summary=summary,
        probability_explanation=ProbabilityExplanation(
            market_yes_percent=market_pct,
            estimated_yes_percent=model_yes_pct,
            estimated_no_percent=model_no_pct,
            confidence_percent=round(prediction.confidence_score, 1),
            net_edge_percentage_points=edge_pp,
        ),
        supports_yes=supports_yes,
        supports_no=supports_no,
        uncertainties=uncertainties,
        data_gaps=data_gaps,
        historical_context=(
            f"In {prediction.comparable_sample_size} vergleichbaren, bereits aufgelösten Fällen lag die "
            f"beobachtete YES-Quote bei {prediction.observed_historical_yes_rate:.0%}."
            if prediction.observed_historical_yes_rate is not None
            else "Keine ausreichende historische Vergleichsbasis vorhanden."
        ),
        recommendation_explanation=recommendation_explanation,
        warning="Prognose, keine Gewissheit. Regelbasierte Erklärung (keine KI-Anfrage durchgeführt).",
        what_we_know=what_we_know,
        divergence_explanation=divergence_explanation,
        change_triggers=list(prediction.change_triggers),
    )
