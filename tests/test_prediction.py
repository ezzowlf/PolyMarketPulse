import sqlite3

import pytest

from polymarketpulse.prediction import (
    MIN_COMPARABLE_SAMPLE,
    MIN_CONFIDENCE_FOR_ACTION,
    compute_prediction,
)


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


def _seed_resolved(conn, n_yes: int, n_no: int, category="esports", provider="polymarket"):
    for i in range(n_yes):
        pmid = f"yes-{i}"
        conn.execute("INSERT INTO markets VALUES (?, ?, ?, ?)", (pmid, provider, pmid, category))
        conn.execute(
            "INSERT INTO market_resolutions VALUES (?, ?, 'resolved', 'Yes')", (provider, pmid)
        )
    for i in range(n_no):
        pmid = f"no-{i}"
        conn.execute("INSERT INTO markets VALUES (?, ?, ?, ?)", (pmid, provider, pmid, category))
        conn.execute(
            "INSERT INTO market_resolutions VALUES (?, ?, 'resolved', 'No')", (provider, pmid)
        )
    conn.commit()


def test_insufficient_data_below_min_sample(conn) -> None:
    result = compute_prediction(
        conn, "m1", "polymarket", "m1", "esports", 0.5, 50000, 90, 0, None, True
    )
    assert result.comparable_sample_size < MIN_COMPARABLE_SAMPLE
    assert result.recommendation == "INSUFFICIENT_DATA"
    assert result.estimated_yes_probability == 0.5  # falls back to market price


def test_estimated_probability_blends_toward_historical_rate(conn) -> None:
    _seed_resolved(conn, n_yes=15, n_no=5)  # 75% historical YES rate
    result = compute_prediction(
        conn, "m2", "polymarket", "m2", "esports", 0.5, 100000, 90, 2, 0.8, True
    )
    assert result.comparable_sample_size == 20
    assert result.estimated_yes_probability is not None
    # Blended estimate should sit between market price (0.5) and historical rate (0.75).
    assert 0.5 < result.estimated_yes_probability < 0.75


def test_edges_never_change_after_computation_are_consistent(conn) -> None:
    _seed_resolved(conn, n_yes=15, n_no=5)
    result = compute_prediction(conn, "m3", "polymarket", "m3", "esports", 0.5, 100000, 90, 1, 0.7, True)
    expected_gross = round(result.estimated_yes_probability - 0.5, 4)
    assert result.gross_yes_edge == expected_gross


def test_strong_recommendation_for_large_edge(conn) -> None:
    _seed_resolved(conn, n_yes=45, n_no=5)  # 90% historical rate, big gap vs 0.3 market price
    result = compute_prediction(conn, "m4", "polymarket", "m4", "esports", 0.3, 200000, 95, 3, 0.9, True)
    assert result.net_yes_edge is not None and result.net_yes_edge > 0
    assert result.recommendation in ("YES", "STRONG_YES")


def test_no_bet_for_tiny_edge(conn) -> None:
    _seed_resolved(conn, n_yes=10, n_no=10)  # 50/50 historical rate
    result = compute_prediction(conn, "m5", "polymarket", "m5", "esports", 0.5, 100000, 90, 1, 0.7, True)
    assert abs(result.net_yes_edge) < 0.08
    assert result.recommendation == "NO_BET"


def test_low_confidence_forces_no_bet_even_with_edge(conn) -> None:
    # Small comparable sample + poor liquidity/data quality/resolution clarity
    # -> low confidence, even though the (thin) historical rate implies an edge.
    _seed_resolved(conn, n_yes=4, n_no=1)
    result = compute_prediction(
        conn, "m6", "polymarket", "m6", "esports", 0.3, liquidity=100, data_quality_report_score=20,
        news_count=0, news_agreement=None, resolution_rules_present=False,
    )
    assert result.confidence_score < MIN_CONFIDENCE_FOR_ACTION
    assert result.recommendation == "NO_BET"


def test_no_market_price_still_produces_prediction_from_history(conn) -> None:
    _seed_resolved(conn, n_yes=15, n_no=5)
    result = compute_prediction(conn, "m7", "polymarket", "m7", "esports", None, 50000, 90, 0, None, True)
    assert result.market_yes_probability is None
    assert result.estimated_yes_probability == result.observed_historical_yes_rate


def test_data_quality_breakdown_has_six_components(conn) -> None:
    result = compute_prediction(conn, "m8", "polymarket", "m8", "esports", 0.5, 50000, 90, 1, 0.6, True)
    d = result.data_quality.as_dict()
    assert len(d) == 7  # six components + total
    assert d["gesamt"] == result.data_quality.total


def test_confidence_and_probability_are_never_conflated(conn) -> None:
    """A high confidence_score must never itself be mistaken for a
    probability value — the two stay independent numbers."""
    _seed_resolved(conn, n_yes=15, n_no=5)
    result = compute_prediction(conn, "m9", "polymarket", "m9", "esports", 0.5, 200000, 95, 2, 0.9, True)
    assert result.confidence_score != result.estimated_yes_probability
    assert 0 <= result.confidence_score <= 100
    assert result.estimated_yes_probability is None or 0 <= result.estimated_yes_probability <= 1
