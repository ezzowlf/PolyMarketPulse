from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .evidence import IndependentEvidenceResult

Recommendation = Literal[
    "STRONG_YES", "YES", "WATCH_YES", "NO_BET", "WATCH_NO", "NO", "STRONG_NO", "INSUFFICIENT_DATA"
]

PREDICTION_VERSION = "v2"


@dataclass(frozen=True)
class SubmodelEstimate:
    """One ensemble member's independent opinion. `weight` is the *base*
    (pre-normalization) weight the ensemble assigns it; `available` marks
    whether the submodel had enough input to produce a real estimate at all
    (e.g. the history model with zero comparable cases) — unavailable
    submodels are excluded from the blend rather than silently defaulting
    to 0.5, which would quietly bias the ensemble."""

    name: str
    estimated_yes_probability: float | None
    weight: float
    available: bool
    detail: str


@dataclass(frozen=True)
class DataQualityBreakdown:
    """Every component behind the single data_quality_score, so it is never
    a bare number without explanation (per the dashboard requirement)."""

    vollstaendigkeit: float
    aktualitaet: float
    quellenuebereinstimmung: float
    historische_fallzahl: float
    resolution_klarheit: float
    liquiditaet: float

    @property
    def total(self) -> float:
        return round(
            (
                self.vollstaendigkeit
                + self.aktualitaet
                + self.quellenuebereinstimmung
                + self.historische_fallzahl
                + self.resolution_klarheit
                + self.liquiditaet
            )
            / 6,
            1,
        )

    def as_dict(self) -> dict:
        return {
            "vollstaendigkeit": self.vollstaendigkeit,
            "aktualitaet": self.aktualitaet,
            "quellenuebereinstimmung": self.quellenuebereinstimmung,
            "historische_fallzahl": self.historische_fallzahl,
            "resolution_klarheit": self.resolution_klarheit,
            "liquiditaet": self.liquiditaet,
            "gesamt": self.total,
        }


@dataclass(frozen=True)
class ScenarioSet:
    """Deterministic, factor-derived scenario descriptions. Text is built
    from structured inputs (submodel estimates, news events, deadline
    phase) by plain string templates — no LLM involved in deciding what the
    scenarios *are*; GPT is only ever handed this finished set to phrase
    more naturally in the explanation layer."""

    base_case: str
    bull_case: list[str]
    bear_case: list[str]

    def as_dict(self) -> dict:
        return {"base_case": self.base_case, "bull_case": self.bull_case, "bear_case": self.bear_case}


@dataclass(frozen=True)
class PredictionResult:
    """The statistical engine's binding, unmodifiable output. GPT is only
    ever allowed to *explain* these numbers — never to change or invent
    them (enforced by ai/validation.py).

    V2 extends the V1 (base-rate-only) result with an ensemble of
    independent submodels (deadline/momentum/history/news+Bayesian),
    scenario framing, and full submodel transparency — every existing V1
    field is preserved so downstream consumers (ai/service.py, backtest.py,
    the dashboard, existing tests) keep working unchanged; new fields are
    purely additive.
    """

    market_id: str
    market_yes_probability: float | None
    market_no_probability: float | None
    estimated_yes_probability: float | None
    estimated_no_probability: float | None
    gross_yes_edge: float | None
    net_yes_edge: float | None
    confidence_score: float
    data_quality: DataQualityBreakdown
    uncertainty_lower: float | None
    uncertainty_upper: float | None
    recommendation: Recommendation
    comparable_sample_size: int
    observed_historical_yes_rate: float | None
    reasoning_notes: tuple[str, ...] = field(default_factory=tuple)

    # --- V2 additions (all optional / additive) -----------------------
    deadline_phase: str = "unbekannt"
    submodel_estimates: tuple[SubmodelEstimate, ...] = field(default_factory=tuple)
    ensemble_agreement: float | None = None
    scenarios: ScenarioSet | None = None
    news_sentiment_score: float | None = None  # -1 (negativ) .. +1 (positiv)
    news_confirmation_count: int = 0

    # --- Independent Evidence & Early-Signal Engine (additive) ---------
    independent_evidence: IndependentEvidenceResult | None = None

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_yes_probability": self.market_yes_probability,
            "market_no_probability": self.market_no_probability,
            "estimated_yes_probability": self.estimated_yes_probability,
            "estimated_no_probability": self.estimated_no_probability,
            "gross_yes_edge": self.gross_yes_edge,
            "net_yes_edge": self.net_yes_edge,
            "confidence_score": self.confidence_score,
            "data_quality_score": self.data_quality.total,
            "data_quality_breakdown": self.data_quality.as_dict(),
            "uncertainty_lower": self.uncertainty_lower,
            "uncertainty_upper": self.uncertainty_upper,
            "recommendation": self.recommendation,
            "comparable_sample_size": self.comparable_sample_size,
            "observed_historical_yes_rate": self.observed_historical_yes_rate,
            "reasoning_notes": list(self.reasoning_notes),
            "deadline_phase": self.deadline_phase,
            "submodel_estimates": [
                {
                    "name": s.name, "estimated_yes_probability": s.estimated_yes_probability,
                    "weight": s.weight, "available": s.available, "detail": s.detail,
                }
                for s in self.submodel_estimates
            ],
            "ensemble_agreement": self.ensemble_agreement,
            "scenarios": self.scenarios.as_dict() if self.scenarios else None,
            "news_sentiment_score": self.news_sentiment_score,
            "news_confirmation_count": self.news_confirmation_count,
            "independent_evidence": self.independent_evidence.as_dict() if self.independent_evidence else None,
        }
