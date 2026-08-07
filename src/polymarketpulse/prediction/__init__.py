"""Prediction Engine V2 — package public API. Re-exports everything the V1
single-file `prediction.py` used to export, so existing callers
(`ai/service.py`, `ai/fallback.py`, `ai/validation.py`, `backtest.py`,
`scripts/generate_acceptance_examples.py`, `tests/test_prediction.py`)
keep working against `polymarketpulse.prediction` unchanged.
"""

from __future__ import annotations

from .deadline import (
    DEADLINE_PHASES,
    PHASE_LABEL_DE,
    DeadlineWeights,
    classify_deadline_phase,
    compute_deadline_weights,
    deadline_weights_for,
)
from .engine import (
    EDGE_NO_BET,
    EDGE_STRONG,
    EDGE_WATCH,
    MIN_COMPARABLE_SAMPLE,
    MIN_CONFIDENCE_FOR_ACTION,
    PREDICTION_VERSION,
    _recommendation,
    compute_prediction,
    market_blind_forecast,
)
from .types import (
    ContributionEntry,
    DataQualityBreakdown,
    ForecastStatus,
    PredictionResult,
    Recommendation,
    ScenarioSet,
    SubmodelEstimate,
)

__all__ = [
    "DEADLINE_PHASES",
    "EDGE_NO_BET",
    "EDGE_STRONG",
    "EDGE_WATCH",
    "MIN_COMPARABLE_SAMPLE",
    "MIN_CONFIDENCE_FOR_ACTION",
    "PHASE_LABEL_DE",
    "PREDICTION_VERSION",
    "ContributionEntry",
    "DataQualityBreakdown",
    "DeadlineWeights",
    "ForecastStatus",
    "PredictionResult",
    "Recommendation",
    "ScenarioSet",
    "SubmodelEstimate",
    "_recommendation",
    "classify_deadline_phase",
    "compute_deadline_weights",
    "compute_prediction",
    "deadline_weights_for",
    "market_blind_forecast",
]
