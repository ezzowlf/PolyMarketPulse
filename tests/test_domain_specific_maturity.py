from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from polymarketpulse.prediction.engine import compute_prediction
from polymarketpulse.providers.fedboard import FedPolicyDecision
from polymarketpulse.providers.fred import MacroSnapshot
from polymarketpulse.storage import Storage


def _base(tmp_path, name: str):
    return Storage(tmp_path / f"{name}.db").connection


def test_supported_quant_without_history_or_news(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine._fetch_quant_snapshot",
        lambda _asset: SimpleNamespace(current_price=60_000.0, daily_volatility=0.02),
    )
    result = compute_prediction(
        _base(tmp_path, "quant"),
        market_id="btc-future", provider="polymarket", provider_market_id="btc-future",
        category="BTC above 100000", classified_category="CRYPTO",
        market_yes_price=0.05, liquidity=250_000, data_quality_report_score=95,
        news_count=0, news_agreement=None, resolution_rules_present=True,
        question="Will the price of Bitcoin be above $100,000 on 2026-12-31?",
        resolution_text=(
            "Resolves YES if the Binance BTC/USDT one-minute candle close at 12:00 ET "
            "on 2026-12-31 is above $100,000; otherwise resolves NO. According to Binance."
        ),
    )

    assert result.comparable_sample_size == 0
    assert result.independent_evidence is not None and not result.independent_evidence.available
    assert result.world_state is not None and result.world_state.state_variables
    assert result.forecast_maturity == "SUPPORTED_FORECAST", (
        result.forecast_status, result.independent_probability, result.confidence_score,
        result.data_quality_composite, result.data_gaps,
        [(s.name, s.available, s.detail) for s in result.submodel_estimates],
        [note for note in result.reasoning_notes if "quant" in note.lower() or "special" in note.lower()],
    )


def test_exact_fed_shadow_stays_unpublished_without_independent_evidence(tmp_path, monkeypatch) -> None:
    snapshot = MacroSnapshot(
        policy_rate=4.0, policy_rate_as_of=date(2026, 7, 1),
        cpi_yoy=2.2, cpi_yoy_prior=3.1,
        unemployment_rate=4.8, unemployment_rate_prior=4.1,
        as_of_date=date(2026, 8, 1), next_fomc_meeting_date=date(2026, 9, 16),
    )
    monkeypatch.setattr("polymarketpulse.prediction.engine._fetch_macro_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        "polymarketpulse.providers.fedboard.fetch_latest_policy_decision",
        lambda: FedPolicyDecision("UNCHANGED", date(2026, 7, 29), 3.5, 3.75, "https://fed.example/july", "2026-08-14T00:00:00+00:00"),
    )
    result = compute_prediction(
        _base(tmp_path, "macro"),
        market_id="fed-future", provider="polymarket", provider_market_id="fed-future",
        category="Fed September hold", classified_category="CENTRAL_BANKS",
        market_yes_price=0.22, liquidity=250_000, data_quality_report_score=95,
        news_count=0, news_agreement=None, resolution_rules_present=True,
        question="Will there be no change in Fed interest rates after the September 2026 meeting?",
        resolution_text=(
            "Resolves YES if the Federal Reserve target range is unchanged after its September 2026 meeting; "
            "otherwise resolves NO. According to the Federal Reserve statement."
        ),
    )

    assert result.comparable_sample_size == 0
    assert result.independent_evidence is not None and not result.independent_evidence.available
    assert result.world_state is not None and result.world_state.state_variables
    assert result.forecast_maturity == "NO_FORECAST", (
        result.forecast_status, result.independent_probability, result.confidence_score,
        result.data_quality_composite, result.data_gaps, result.submodel_estimates,
    )
    assert result.model_hypothesis_probability == 0.7142857142857143
    assert result.published_forecast_probability is None


def test_geopolitics_without_evidence_remains_context_only(tmp_path) -> None:
    result = compute_prediction(
        _base(tmp_path, "geo"),
        market_id="geo", provider="polymarket", provider_market_id="geo",
        category="Strait traffic normal", classified_category="GEOPOLITICS",
        market_yes_price=0.4, liquidity=250_000, data_quality_report_score=95,
        news_count=0, news_agreement=None, resolution_rules_present=True,
        question="Will Strait traffic return to normal by December 31, 2026?",
        resolution_text="Resolves YES if normal commercial traffic is restored by December 31, 2026.",
    )

    assert result.forecast_maturity in {"NO_FORECAST", "CONTEXT_ONLY"}
    assert not result.world_state or not result.world_state.state_variables


def test_confidence_is_independent_of_probability_magnitude(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine._fetch_quant_snapshot",
        lambda _asset: SimpleNamespace(current_price=60_000.0, daily_volatility=0.02),
    )
    common = {
        "provider": "polymarket", "category": "btc", "classified_category": "CRYPTO",
        "liquidity": 250_000, "data_quality_report_score": 95, "news_count": 0,
        "news_agreement": None, "resolution_rules_present": True,
        "resolution_text": "Resolves YES according to Binance; otherwise resolves NO.",
    }
    low = compute_prediction(
        _base(tmp_path, "low"), market_id="low", provider_market_id="low",
        market_yes_price=0.05,
        question="Will Bitcoin be above $100,000 on 2026-12-31?", **common,
    )
    high = compute_prediction(
        _base(tmp_path, "high"), market_id="high", provider_market_id="high",
        market_yes_price=0.95,
        question="Will Bitcoin be above $50,000 on 2026-12-31?", **common,
    )

    assert low.confidence_score == high.confidence_score
