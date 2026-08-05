"""Unit tests for the individual Prediction Engine V2 submodules — each
tested independently of the orchestrator (engine.py, already covered by
tests/test_prediction.py's integration-style tests)."""

from datetime import UTC, datetime, timedelta

from polymarketpulse.prediction.bayesian import bayesian_update
from polymarketpulse.prediction.confidence import compute_confidence
from polymarketpulse.prediction.deadline import classify_deadline_phase, deadline_weights_for
from polymarketpulse.prediction.ensemble import combine_submodels
from polymarketpulse.prediction.momentum import compute_momentum_estimate
from polymarketpulse.prediction.news import NewsEvidenceItem, compute_news_estimate, score_sentiment
from polymarketpulse.prediction.scenarios import build_scenarios
from polymarketpulse.prediction.types import DataQualityBreakdown, SubmodelEstimate
from polymarketpulse.price_analytics import PricePoint

# --- Deadline Engine ---------------------------------------------------


def test_deadline_phase_more_than_seven_days():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert classify_deadline_phase(now, now + timedelta(days=10)) == "MORE_THAN_7_DAYS"


def test_deadline_phase_final_minutes():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert classify_deadline_phase(now, now + timedelta(seconds=30)) == "FINAL_MINUTES"


def test_deadline_phase_past_resolution():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert classify_deadline_phase(now, now - timedelta(hours=1)) == "RESOLVED_OR_PAST"


def test_deadline_phase_unknown_without_resolution_date():
    assert classify_deadline_phase(datetime.now(UTC), None) == "UNKNOWN"


def test_deadline_weights_increase_news_weight_near_resolution():
    far = deadline_weights_for("MORE_THAN_7_DAYS")
    near = deadline_weights_for("FINAL_MINUTES")
    assert near.news_weight > far.news_weight
    assert near.history_weight < far.history_weight
    assert near.recommended_scan_interval_seconds < far.recommended_scan_interval_seconds


# --- Momentum submodel ---------------------------------------------------


def test_momentum_returns_bare_market_price_with_thin_history():
    estimate, analytics, detail = compute_momentum_estimate([], 0.42)
    assert estimate == 0.42
    assert analytics is None
    assert "Marktpreis unangepasst" in detail


def test_momentum_none_without_market_price():
    estimate, _analytics, _detail = compute_momentum_estimate([], None)
    assert estimate is None


def test_momentum_adjustment_stays_within_cap():
    points = [
        PricePoint(captured_at=(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i)).isoformat(), yes_price=p)
        for i, p in enumerate([0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6])
    ]
    estimate, _analytics, _detail = compute_momentum_estimate(points, 0.6)
    assert estimate is not None
    assert abs(estimate - 0.6) <= 0.05 + 1e-9


# --- News submodel ---------------------------------------------------


def test_score_sentiment_positive():
    score, terms = score_sentiment("Deal reached, agreement confirmed by both sides")
    assert score > 0
    assert terms


def test_score_sentiment_negative():
    score, _terms = score_sentiment("Talks collapse, deal rejected and cancelled")
    assert score < 0


def test_score_sentiment_neutral_without_matches():
    score, terms = score_sentiment("Market update for Tuesday")
    assert score == 0.0
    assert terms == ()


def test_news_estimate_unavailable_without_evidence():
    estimate, sentiment, confirmations = compute_news_estimate([], 0.5)
    assert estimate.available is False
    assert sentiment is None
    assert confirmations == 0


def test_news_estimate_positive_sentiment_pushes_estimate_up():
    evidence = [
        NewsEvidenceItem(
            news_event_id=1, title="Deal confirmed", source="reuters", sentiment=0.8,
            matched_terms=("confirmed",), trust=0.95, recency_weight=1.0, confidence=0.9, combined_weight=0.855,
        ),
        NewsEvidenceItem(
            news_event_id=2, title="Agreement reached", source="bloomberg", sentiment=0.8,
            matched_terms=("agreement",), trust=0.9, recency_weight=1.0, confidence=0.9, combined_weight=0.81,
        ),
    ]
    estimate, _sentiment, confirmations = compute_news_estimate(evidence, 0.5)
    assert estimate.available is True
    assert estimate.estimated_yes_probability > 0.5
    assert confirmations == 2


# --- Bayesian update ---------------------------------------------------


def test_bayesian_update_is_noop_without_news():
    result = bayesian_update(0.6, None, 0, news_weight_multiplier=1.0)
    assert result.posterior_probability == result.prior_probability == 0.6
    assert result.evidence_strength == 0.0


def test_bayesian_update_moves_posterior_toward_positive_sentiment():
    result = bayesian_update(0.5, weighted_news_sentiment=0.8, confirmation_count=3, news_weight_multiplier=1.5)
    assert result.posterior_probability > result.prior_probability


def test_bayesian_update_moves_posterior_toward_negative_sentiment():
    result = bayesian_update(0.5, weighted_news_sentiment=-0.8, confirmation_count=3, news_weight_multiplier=1.5)
    assert result.posterior_probability < result.prior_probability


def test_bayesian_update_capped_shift():
    result = bayesian_update(0.5, weighted_news_sentiment=1.0, confirmation_count=99, news_weight_multiplier=10.0)
    assert result.posterior_probability < 1.0
    assert result.evidence_strength <= 1.5 + 1e-9


# --- Confidence ---------------------------------------------------


def _dq(total=80.0) -> DataQualityBreakdown:
    return DataQualityBreakdown(
        vollstaendigkeit=total, aktualitaet=total, quellenuebereinstimmung=total,
        historische_fallzahl=total, resolution_klarheit=total, liquiditaet=total,
    )


def test_confidence_higher_with_more_agreeing_submodels():
    one_model = [SubmodelEstimate("history", 0.6, 0.6, True, "x")]
    two_agreeing = [
        SubmodelEstimate("history", 0.6, 0.6, True, "x"),
        SubmodelEstimate("momentum", 0.61, 0.4, True, "y"),
    ]
    conf_one, _ = compute_confidence(_dq(), one_model, market_stability=1.0, deadline_phase_known=True)
    conf_two, agreement = compute_confidence(_dq(), two_agreeing, market_stability=1.0, deadline_phase_known=True)
    assert conf_two > conf_one
    assert agreement is not None and agreement > 0.9


def test_confidence_lower_when_submodels_disagree():
    agreeing = [
        SubmodelEstimate("history", 0.6, 0.6, True, "x"),
        SubmodelEstimate("momentum", 0.61, 0.4, True, "y"),
    ]
    disagreeing = [
        SubmodelEstimate("history", 0.2, 0.6, True, "x"),
        SubmodelEstimate("momentum", 0.9, 0.4, True, "y"),
    ]
    conf_agree, _ = compute_confidence(_dq(), agreeing, market_stability=1.0, deadline_phase_known=True)
    conf_disagree, _ = compute_confidence(_dq(), disagreeing, market_stability=1.0, deadline_phase_known=True)
    assert conf_agree > conf_disagree


def test_confidence_never_exceeds_100():
    many = [SubmodelEstimate(f"m{i}", 0.5, 1.0, True, "x") for i in range(10)]
    conf, _ = compute_confidence(_dq(100.0), many, market_stability=1.0, deadline_phase_known=True)
    assert conf <= 100.0


# --- Ensemble ---------------------------------------------------


def test_ensemble_excludes_unavailable_submodels():
    estimates = [
        SubmodelEstimate("history", None, 0.0, False, "n/a"),
        SubmodelEstimate("momentum", 0.7, 0.5, True, "x"),
    ]
    blended, all_estimates = combine_submodels(estimates)
    assert blended == 0.7
    assert len(all_estimates) == 2


def test_ensemble_none_when_nothing_available():
    estimates = [SubmodelEstimate("history", None, 0.0, False, "n/a")]
    blended, _ = combine_submodels(estimates)
    assert blended is None


def test_ensemble_weighted_average():
    estimates = [
        SubmodelEstimate("history", 0.8, 0.6, True, "x"),
        SubmodelEstimate("momentum", 0.4, 0.4, True, "y"),
    ]
    blended, _ = combine_submodels(estimates)
    assert blended == round(0.8 * 0.6 + 0.4 * 0.4, 4)


# --- Scenarios ---------------------------------------------------


def test_scenarios_empty_without_estimate():
    result = build_scenarios(None, [], [], 0, "INSUFFICIENT_DATA")
    assert result.bull_case == []
    assert result.bear_case == []
    assert "Keine belastbare" in result.base_case


def test_scenarios_always_have_content_when_estimate_exists():
    submodels = [SubmodelEstimate("history", 0.65, 0.6, True, "hist"), SubmodelEstimate("momentum", 0.6, 0.4, True, "mom")]
    result = build_scenarios(0.62, submodels, [], 12, "YES")
    assert result.base_case
    assert len(result.bull_case) >= 1
    assert len(result.bear_case) >= 1
