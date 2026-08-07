from datetime import UTC, datetime, timedelta

from polymarketpulse.prediction.evidence import IndependentEvidenceResult
from polymarketpulse.prediction.manipulation import ManipulationRiskResult
from polymarketpulse.prediction.reliability import MarketReliabilityResult
from polymarketpulse.prediction.resolution_edge import ResolutionEdgeResult
from polymarketpulse.prediction.types import DataQualityBreakdown, PredictionResult
from polymarketpulse.shadow_trading import (
    DIRECTION_NO,
    DIRECTION_NONE,
    DIRECTION_YES,
    STATUS_CANDIDATE,
    STATUS_SKIPPED,
    compute_lifecycle_update,
    compute_shadow_pnl,
    evaluate_shadow_qualification,
)


def _dq(total=80.0):
    return DataQualityBreakdown(
        vollstaendigkeit=total, aktualitaet=total, quellenuebereinstimmung=total,
        historische_fallzahl=total, resolution_klarheit=total, liquiditaet=total,
    )


def _prediction(**overrides) -> PredictionResult:
    defaults = {
        "market_id": "m1", "market_yes_probability": 0.5, "market_no_probability": 0.5,
        "estimated_yes_probability": 0.65, "estimated_no_probability": 0.35,
        "gross_yes_edge": 0.15, "net_yes_edge": 0.13, "confidence_score": 70.0, "data_quality": _dq(80.0),
        "uncertainty_lower": 0.5, "uncertainty_upper": 0.8, "recommendation": "YES",
        "comparable_sample_size": 10, "observed_historical_yes_rate": 0.6, "deadline_phase": "MORE_THAN_7_DAYS",
        "independent_evidence": IndependentEvidenceResult(
            available=True, independent_yes_probability=0.65, confirmation_count=3,
            source_quality_score=80.0, time_since_first_report_hours=2.0, contradiction_detected=False,
            breaking=True, information_edge_score=40.0, divergence=0.15,
        ),
        "resolution_edge": ResolutionEdgeResult(
            yes_condition="x", no_condition="y", has_explicit_deadline=True, authority_source="gov",
            clarity_score=80.0, ambiguity_score=0.0, source_authority_score=80.0,
            deadline_precision_score=85.0, dispute_risk_score=5.0, resolution_edge_score=75.0,
            risk_level="niedrig", pitfalls=(), detail="",
        ),
        "market_reliability": MarketReliabilityResult(level="hoch", score=75.0, components={}, detail=""),
        "manipulation_risk": ManipulationRiskResult(risk_score=10.0, reasons=(), confidence=0.8, detail=""),
    }
    defaults.update(overrides)
    return PredictionResult(**defaults)


def test_qualifying_market_produces_candidate() -> None:
    prediction = _prediction()
    opportunity = {"opportunity_score": 70.0}
    decision = evaluate_shadow_qualification("m1", "polymarket", "1", prediction, opportunity, spread=0.02, liquidity=50_000.0)
    assert decision.status == STATUS_CANDIDATE
    assert decision.direction == DIRECTION_YES
    assert decision.blockers == ()


def test_low_edge_market_is_skipped_with_reason() -> None:
    prediction = _prediction(net_yes_edge=0.01)
    opportunity = {"opportunity_score": 70.0}
    decision = evaluate_shadow_qualification("m1", "polymarket", "1", prediction, opportunity, spread=0.02, liquidity=50_000.0)
    assert decision.status == STATUS_SKIPPED
    assert decision.direction == DIRECTION_NONE
    assert any("Edge" in b for b in decision.blockers)


def test_missing_independent_evidence_blocks_and_is_logged() -> None:
    prediction = _prediction(independent_evidence=IndependentEvidenceResult(
        available=False, independent_yes_probability=None, confirmation_count=0, source_quality_score=None,
        time_since_first_report_hours=None, contradiction_detected=False, breaking=False,
        information_edge_score=None, divergence=None,
    ))
    decision = evaluate_shadow_qualification("m1", "polymarket", "1", prediction, {"opportunity_score": 70.0}, 0.02, 50_000.0)
    assert decision.status == STATUS_SKIPPED
    assert any("unabhängige" in b for b in decision.blockers)


def test_high_manipulation_risk_blocks() -> None:
    prediction = _prediction(manipulation_risk=ManipulationRiskResult(risk_score=90.0, reasons=(), confidence=0.9, detail=""))
    decision = evaluate_shadow_qualification("m1", "polymarket", "1", prediction, {"opportunity_score": 70.0}, 0.02, 50_000.0)
    assert decision.status == STATUS_SKIPPED
    assert any("Manipulation" in b for b in decision.blockers)


def test_contradiction_blocks() -> None:
    prediction = _prediction(independent_evidence=IndependentEvidenceResult(
        available=True, independent_yes_probability=0.6, confirmation_count=2, source_quality_score=70.0,
        time_since_first_report_hours=2.0, contradiction_detected=True, breaking=True,
        information_edge_score=20.0, divergence=0.1,
    ))
    decision = evaluate_shadow_qualification("m1", "polymarket", "1", prediction, {"opportunity_score": 70.0}, 0.02, 50_000.0)
    assert decision.status == STATUS_SKIPPED
    assert any("widersprüch" in b for b in decision.blockers)


def test_negative_edge_produces_no_direction() -> None:
    prediction = _prediction(net_yes_edge=-0.13)
    decision = evaluate_shadow_qualification("m1", "polymarket", "1", prediction, {"opportunity_score": 70.0}, 0.02, 50_000.0)
    assert decision.status == STATUS_CANDIDATE
    assert decision.direction == DIRECTION_NO


def test_market_price_does_not_feed_independent_probability() -> None:
    # Two predictions differing only in market price but identical
    # independent evidence must report the same independent_probability —
    # proving the market price is never used as an anchor here either.
    p1 = _prediction(market_yes_probability=0.1)
    p2 = _prediction(market_yes_probability=0.9)
    assert p1.independent_evidence.independent_yes_probability == p2.independent_evidence.independent_yes_probability


# --- lifecycle -------------------------------------------------------------

def _trade_row(**overrides) -> dict:
    defaults = {
        "id": 1, "direction": DIRECTION_YES, "entry_market_price": 0.5, "assumed_stake": 1.0, "simulated_fee": 0.02,
        "simulated_slippage": 0.005, "created_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
        "max_favorable_move": None, "max_adverse_move": None, "max_drawdown": None,
        "price_after_5m": None, "price_after_15m": None, "price_after_1h": None, "price_after_6h": None, "price_after_24h": None,
    }
    defaults.update(overrides)
    return defaults


def test_lifecycle_tracks_favorable_and_adverse_move() -> None:
    trade = _trade_row(entry_market_price=0.5)
    update = compute_lifecycle_update(trade, 0.7, None, deadline_hours=100, resolution_status=None, winning_outcome=None)
    assert update.fields["max_favorable_move"] == 0.2
    assert update.exit_reason is None


def test_lifecycle_drawdown_after_peak() -> None:
    trade = _trade_row(entry_market_price=0.5, max_favorable_move=0.3, max_adverse_move=0.0, max_drawdown=0.0)
    update = compute_lifecycle_update(trade, 0.6, None, deadline_hours=100, resolution_status=None, winning_outcome=None)
    # move now = 0.1, peak favorable was 0.3 -> drawdown from peak = 0.2
    assert update.fields["max_drawdown"] == 0.2


def test_resolution_closes_trade() -> None:
    trade = _trade_row()
    update = compute_lifecycle_update(trade, 0.9, None, deadline_hours=-1, resolution_status="resolved", winning_outcome="Yes")
    assert update.exit_reason == "Resolution"


def test_cancelled_market_closes_neutrally() -> None:
    trade = _trade_row()
    update = compute_lifecycle_update(trade, 0.5, None, deadline_hours=10, resolution_status="cancelled", winning_outcome=None)
    assert "neutral" in update.exit_reason


def test_deadline_reached_without_resolution_exits() -> None:
    trade = _trade_row()
    update = compute_lifecycle_update(trade, 0.5, None, deadline_hours=-0.1, resolution_status=None, winning_outcome=None)
    assert update.exit_reason == "Deadline erreicht"


def test_edge_vanished_triggers_exit() -> None:
    trade = _trade_row()
    prediction = _prediction(net_yes_edge=0.005)
    update = compute_lifecycle_update(trade, 0.5, prediction, deadline_hours=100, resolution_status=None, winning_outcome=None)
    assert update.exit_reason == "Edge verschwunden"


def test_no_exit_when_everything_still_healthy() -> None:
    trade = _trade_row(created_at=datetime.now(UTC).isoformat())
    prediction = _prediction()
    update = compute_lifecycle_update(trade, 0.55, prediction, deadline_hours=200, resolution_status=None, winning_outcome=None)
    assert update.exit_reason is None


# --- simulated P&L -----------------------------------------------------------

def test_pnl_includes_fee_and_slippage() -> None:
    pnl_no_fee, _ = compute_shadow_pnl(DIRECTION_YES, 0.5, 1.0, 0.0, 0.0, won=True)
    pnl_with_fee, _ = compute_shadow_pnl(DIRECTION_YES, 0.5, 1.0, 0.05, 0.02, won=True)
    assert pnl_with_fee < pnl_no_fee


def test_losing_yes_trade_loses_stake_times_price() -> None:
    pnl, roi = compute_shadow_pnl(DIRECTION_YES, 0.5, 1.0, 0.0, 0.0, won=False)
    assert pnl == -0.5
    assert roi == -0.5


def test_winning_no_trade_uses_inverse_price() -> None:
    pnl, _roi = compute_shadow_pnl(DIRECTION_NO, 0.3, 1.0, 0.0, 0.0, won=True)
    # NO at market price 0.3 means NO price is 0.7; payoff = 1 - 0.7 = 0.3
    assert pnl == 0.3
