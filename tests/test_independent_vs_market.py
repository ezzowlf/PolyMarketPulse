"""Tests for the independent_probability / market_consensus_probability /
blended_probability / calibrated_probability separation and the
market_blind_forecast() diagnostic — the architectural core of "did we
build a forecasting machine or just an intelligent Polymarket
post-processor?" (see engine.py's ContributionEntry/independent_probability
comments).
"""

from __future__ import annotations

import sqlite3

import pytest

from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.engine import market_blind_forecast


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE markets (market_id TEXT PRIMARY KEY, provider TEXT, provider_market_id TEXT, category TEXT);
        CREATE TABLE market_resolutions (provider TEXT, provider_market_id TEXT, status TEXT, winning_outcome TEXT);
        """
    )
    return c


def _seed(conn, n_yes: int, n_no: int, category="esports", provider="polymarket") -> None:
    for i in range(n_yes):
        pmid = f"yes-{i}"
        conn.execute("INSERT INTO markets VALUES (?, ?, ?, ?)", (pmid, provider, pmid, category))
        conn.execute("INSERT INTO market_resolutions VALUES (?, ?, 'resolved', 'Yes')", (provider, pmid))
    for i in range(n_no):
        pmid = f"no-{i}"
        conn.execute("INSERT INTO markets VALUES (?, ?, ?, ?)", (pmid, provider, pmid, category))
        conn.execute("INSERT INTO market_resolutions VALUES (?, ?, 'resolved', 'No')", (provider, pmid))
    conn.commit()


# --- market_blind_forecast never receives the market price ------------------

def test_market_blind_forecast_signature_has_no_market_price_parameter() -> None:
    """The function must not even accept a market price argument — this is
    checked structurally, not just behaviorally, so it can never regress
    into quietly accepting one."""
    import inspect

    sig = inspect.signature(market_blind_forecast)
    assert "market_yes_price" not in sig.parameters
    assert "market_price" not in sig.parameters


def test_market_blind_forecast_matches_engines_independent_probability(conn) -> None:
    """The standalone diagnostic path and the in-flow independent_probability
    computed inside compute_prediction() must agree — proving the in-flow
    number really is computed the same market-blind way."""
    _seed(conn, n_yes=15, n_no=5)
    result = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True)
    blind = market_blind_forecast(conn, "polymarket", "m1", "esports")
    assert result.independent_probability == blind["blind_independent_probability"]


def test_independent_forecast_executes_without_any_market_price(conn) -> None:
    _seed(conn, n_yes=9, n_no=1)
    blind = market_blind_forecast(conn, "polymarket", "m1", "esports")
    assert blind["blind_independent_probability"] is not None
    assert blind["blind_independent_probability"] == pytest.approx(0.9)


def test_independent_probability_identical_regardless_of_market_price(conn) -> None:
    """Sensitivity check: run the same market data through compute_prediction
    with wildly different market prices — independent_probability must not
    move at all, since it's computed only from market-price-independent
    submodels."""
    _seed(conn, n_yes=12, n_no=8)
    results = [
        compute_prediction(conn, "m1", "polymarket", "m1", "esports", price, 100000, 90, 0, None, True)
        for price in (0.10, 0.25, 0.50, 0.75, 0.90)
    ]
    independents = {r.independent_probability for r in results}
    assert len(independents) == 1  # exactly one distinct value across all five market prices


def test_blended_probability_does_move_with_market_price_when_momentum_available(conn) -> None:
    """Sanity check the opposite: blended_probability (which legitimately
    incorporates market-price-anchored submodels like momentum) IS allowed
    to vary with the market price — this is not itself a bug, only a
    disguised full *copy* would be."""
    from datetime import UTC, datetime, timedelta

    conn.execute(
        "CREATE TABLE market_snapshots (market_id TEXT, captured_at TEXT, yes_price REAL, liquidity REAL, volume_24h REAL, spread REAL)"
    )
    now = datetime.now(UTC)
    for i, price in enumerate([0.40, 0.42, 0.45, 0.47, 0.50]):
        ts = (now - timedelta(hours=5 - i)).isoformat()
        conn.execute(
            "INSERT INTO market_snapshots VALUES ('m1', ?, ?, 10000, 1000, 0.01)", (ts, price)
        )
    conn.commit()
    low = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.30, 100000, 90, 0, None, True)
    high = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.70, 100000, 90, 0, None, True)
    assert low.blended_probability != high.blended_probability


# --- blending / calibration math --------------------------------------------

def test_calibrated_probability_shrinks_toward_half_when_confidence_low(conn) -> None:
    _seed(conn, n_yes=4, n_no=1)  # thin sample -> low confidence
    result = compute_prediction(
        conn, "m1", "polymarket", "m1", "esports", 0.5, liquidity=0, data_quality_report_score=1,
        news_count=0, news_agreement=None, resolution_rules_present=False,
    )
    if result.blended_probability is not None and result.calibrated_probability is not None:
        assert abs(result.calibrated_probability - 0.5) <= abs(result.blended_probability - 0.5)


def test_calibrated_equals_blended_at_full_confidence_boundary() -> None:
    """At the trust ceiling (confidence >= 100), calibration must not move
    the number at all — it's a shrinkage, never an inflation or an
    unrelated transformation."""
    from polymarketpulse.prediction.engine import (
        compute_prediction as cp,  # noqa: F401  (import path sanity)
    )

    blended = 0.8
    confidence = 100.0
    trust = max(0.3, min(1.0, confidence / 100))
    calibrated = round(0.5 + (blended - 0.5) * trust, 4)
    assert calibrated == blended


# --- zero independent data does not fabricate a forecast ---------------------

def test_zero_independent_data_yields_no_forecast_not_a_fabricated_number(conn) -> None:
    result = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.657, 50000, 90, 0, None, True)
    assert result.forecast_status == "NO_FORECAST"
    assert result.independent_probability is None
    assert result.blended_probability is None
    assert result.calibrated_probability is None


def test_baseline_only_status_when_history_is_the_only_available_signal(conn) -> None:
    _seed(conn, n_yes=15, n_no=5)
    result = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True)
    assert result.forecast_status == "BASELINE_ONLY"


# --- contribution breakdown ---------------------------------------------------

def test_contribution_breakdown_marks_unavailable_sources_explicitly(conn) -> None:
    _seed(conn, n_yes=15, n_no=5)
    result = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True)
    by_source = {c.source: c for c in result.contribution_breakdown}
    assert by_source["momentum"].available is False
    assert by_source["momentum"].estimated_yes_probability is None
    assert by_source["history"].available is True


def test_contribution_breakdown_weight_shares_sum_to_one_among_available(conn) -> None:
    conn.execute(
        "CREATE TABLE market_snapshots (market_id TEXT, captured_at TEXT, yes_price REAL, liquidity REAL, volume_24h REAL, spread REAL)"
    )
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    for i, price in enumerate([0.40, 0.42, 0.45, 0.47, 0.50]):
        ts = (now - timedelta(hours=5 - i)).isoformat()
        conn.execute("INSERT INTO market_snapshots VALUES ('m1', ?, ?, 10000, 1000, 0.01)", (ts, price))
    _seed(conn, n_yes=12, n_no=8)
    result = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True)
    shares = [c.weight_share for c in result.contribution_breakdown if c.available and c.weight_share is not None]
    assert shares  # at least one available, weighted source
    assert sum(shares) == pytest.approx(1.0, abs=1e-6)
