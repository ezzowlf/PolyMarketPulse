"""Block E Part 1: Decision Engine. Real, constructed tests proving the
hard rule (large model_hypothesis deviation ALONE never exceeds WATCH) and
the basic state ladder, using minimal PredictionResult fixtures rather than
the full engine pipeline."""

from __future__ import annotations

from dataclasses import replace

import pytest

from polymarketpulse.prediction.decision import compute_decision_state
from polymarketpulse.prediction.types import PredictionResult

BASE_KWARGS = {
    "market_id": "test:1",
    "market_yes_probability": 0.5,
    "market_no_probability": 0.5,
    "estimated_yes_probability": 0.5,
    "estimated_no_probability": 0.5,
    "gross_yes_edge": 0.0,
    "net_yes_edge": 0.0,
    "confidence_score": 80.0,
    "data_quality": None,
    "uncertainty_lower": None,
    "uncertainty_upper": None,
    "recommendation": "WATCH_YES",
    "comparable_sample_size": 10,
    "observed_historical_yes_rate": None,
}


@pytest.fixture
def base_result():
    from polymarketpulse.prediction.types import DataQualityBreakdown

    dq = DataQualityBreakdown(
        vollstaendigkeit=50.0, aktualitaet=50.0, quellenuebereinstimmung=50.0,
        historische_fallzahl=50.0, resolution_klarheit=50.0, liquiditaet=50.0,
    )
    kwargs = dict(BASE_KWARGS)
    kwargs["data_quality"] = dq
    return PredictionResult(**kwargs)


def test_no_published_forecast_no_model_hypothesis_is_no_position(base_result):
    result = replace(base_result, published_forecast_probability=None, model_hypothesis_probability=None)
    state, reasons = compute_decision_state(result)
    assert state == "NO_POSITION"
    assert reasons


def test_large_model_hypothesis_deviation_alone_never_exceeds_watch(base_result):
    """The hard rule: model_hypothesis_probability=0.95 vs market=0.10 is a
    massive raw deviation, but published_forecast_probability is None (not
    evidence-backed/publishable) — must cap at WATCH, never POSSIBLE_EDGE
    or STRONG_EDGE."""
    result = replace(
        base_result,
        published_forecast_probability=None,
        model_hypothesis_probability=0.95,
        market_probability=0.10,
        forecast_maturity="HYPOTHESIS",
    )
    state, reasons = compute_decision_state(result)
    assert state == "WATCH"
    assert any("hard rule" in r for r in reasons)


def test_real_published_small_edge_is_no_position_or_watch(base_result):
    result = replace(
        base_result,
        published_forecast_probability=0.51,
        market_probability=0.50,
        model_hypothesis_probability=0.51,
        forecast_maturity="SUPPORTED_FORECAST",
        confidence_score=80.0,
    )
    state, _ = compute_decision_state(result)
    assert state == "NO_POSITION"


def test_real_published_strong_edge_with_good_maturity_and_confidence_is_strong_edge(base_result):
    result = replace(
        base_result,
        published_forecast_probability=0.70,
        market_probability=0.50,
        model_hypothesis_probability=0.70,
        forecast_maturity="MATURE_FORECAST",
        confidence_score=90.0,
    )
    state, _ = compute_decision_state(result, liquidity=50_000, spread=0.01)
    assert state == "STRONG_EDGE"


def test_strong_edge_capped_at_possible_edge_when_illiquid(base_result):
    result = replace(
        base_result,
        published_forecast_probability=0.70,
        market_probability=0.50,
        model_hypothesis_probability=0.70,
        forecast_maturity="MATURE_FORECAST",
        confidence_score=90.0,
    )
    state, reasons = compute_decision_state(result, liquidity=100, spread=0.01)
    assert state == "POSSIBLE_EDGE"
    assert any("illiquid" in r for r in reasons)


def test_divergence_reject_forces_no_position_even_with_real_published_forecast(base_result):
    from polymarketpulse.prediction.divergence_audit import DivergenceAuditResult

    audit = DivergenceAuditResult(triggered=True, gap=0.3, verdict="REJECT", checks=(), summary="rejected")
    result = replace(
        base_result,
        published_forecast_probability=0.80,
        market_probability=0.50,
        model_hypothesis_probability=0.80,
        forecast_maturity="MATURE_FORECAST",
        confidence_score=95.0,
        divergence_audit=audit,
    )
    state, reasons = compute_decision_state(result)
    assert state == "NO_POSITION"
    assert any("REJECT" in r for r in reasons)
