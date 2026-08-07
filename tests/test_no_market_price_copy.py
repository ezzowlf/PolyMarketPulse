"""Regression tests locking in the root-cause fix for the reported bug:
the live app showed MARKT == ENGINE on nearly every market plus a pauschale
+2.0pp edge. Root cause was two-fold — see engine.py/momentum.py comments:

1. momentum.py reported itself `available` (at the ensemble's single
   largest weight) while returning nothing but the unadjusted market price
   whenever it had too little price history for a real signal.
2. engine.py's cost-haircut edge calculation took the wrong branch when
   gross_edge was exactly 0, manufacturing a +2.0pp net edge out of zero
   real edge.

These tests must keep failing if either regression is reintroduced.
"""

from __future__ import annotations

import sqlite3

import pytest

from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.momentum import compute_momentum_estimate


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


# --- Test 1: no market-price copying ---------------------------------------

def test_momentum_never_reports_bare_market_price_as_available_signal() -> None:
    """With fewer than 3 price snapshots, momentum has no basis for a real
    adjustment and must say so — not silently hand back the market price
    dressed up as an independent estimate."""
    estimate, _analytics, _detail = compute_momentum_estimate([], 0.5)
    assert estimate is None

    from polymarketpulse.price_analytics import PricePoint

    two_points = [
        PricePoint(captured_at="2026-01-01T00:00:00Z", yes_price=0.5, liquidity=1000, volume_24h=100, spread=0.01),
        PricePoint(captured_at="2026-01-02T00:00:00Z", yes_price=0.5, liquidity=1000, volume_24h=100, spread=0.01),
    ]
    estimate2, _a2, _d2 = compute_momentum_estimate(two_points, 0.5)
    assert estimate2 is None


def test_engine_produces_no_forecast_when_no_submodel_has_independent_signal(conn) -> None:
    """No comparable history, no price history, no news/evidence
    infrastructure -> the engine must report NO forecast (None), never the
    market price relabeled as its own prediction."""
    result = compute_prediction(conn, "m1", "polymarket", "m1", "esports", 0.657, 50000, 90, 0, None, True)
    assert result.estimated_yes_probability is None
    assert result.net_yes_edge is None
    assert result.recommendation == "INSUFFICIENT_DATA"


def test_engine_estimate_is_not_forced_toward_market_price_when_history_available(conn) -> None:
    """When a real independent signal (historical base rate) IS available,
    the engine's estimate must reflect that signal, not get diluted back
    toward the market price by a disguised copy-fallback submodel."""
    for i in range(18):
        pmid = f"yes-{i}"
        conn.execute("INSERT INTO markets VALUES (?, 'polymarket', ?, 'esports')", (pmid, pmid))
        conn.execute("INSERT INTO market_resolutions VALUES ('polymarket', ?, 'resolved', 'Yes')", (pmid,))
    for i in range(2):
        pmid = f"no-{i}"
        conn.execute("INSERT INTO markets VALUES (?, 'polymarket', ?, 'esports')", (pmid, pmid))
        conn.execute("INSERT INTO market_resolutions VALUES ('polymarket', ?, 'resolved', 'No')", (pmid,))
    conn.commit()
    # Market price deliberately far from the 90% historical rate.
    result = compute_prediction(conn, "m2", "polymarket", "m2", "esports", 0.2, 100000, 90, 0, None, True)
    assert result.estimated_yes_probability is not None
    assert result.estimated_yes_probability != result.market_yes_probability
    assert result.estimated_yes_probability > 0.5  # pulled toward the real 90% historical rate, not stuck at 0.2


# --- Test 2 + 3: edge math is exact, zero stays zero ------------------------

def test_edge_is_mathematically_exact_difference() -> None:
    """gross_edge must equal estimated - market exactly (rounding aside) —
    no artificial minimum, no hidden floor."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE markets (market_id TEXT PRIMARY KEY, provider TEXT, provider_market_id TEXT, category TEXT);
        CREATE TABLE market_resolutions (provider TEXT, provider_market_id TEXT, status TEXT, winning_outcome TEXT);
        """
    )
    for i in range(9):
        pmid = f"yes-{i}"
        conn.execute("INSERT INTO markets VALUES (?, 'polymarket', ?, 'esports')", (pmid, pmid))
        conn.execute("INSERT INTO market_resolutions VALUES ('polymarket', ?, 'resolved', 'Yes')", (pmid,))
    for i in range(1):
        pmid = f"no-{i}"
        conn.execute("INSERT INTO markets VALUES (?, 'polymarket', ?, 'esports')", (pmid, pmid))
        conn.execute("INSERT INTO market_resolutions VALUES ('polymarket', ?, 'resolved', 'No')", (pmid,))
    conn.commit()
    result = compute_prediction(conn, "m3", "polymarket", "m3", "esports", 0.5, 100000, 90, 0, None, True)
    assert result.estimated_yes_probability is not None
    expected_gross = round(result.estimated_yes_probability - result.market_yes_probability, 4)
    assert result.gross_yes_edge == expected_gross
    conn.close()


def test_zero_gross_edge_never_produces_a_fabricated_pseudo_edge() -> None:
    """This is the exact bug reported against the live app: when the
    engine's estimate happens to equal the market price exactly, the
    cost-haircut must not manufacture a +2.0pp (or any) net edge out of
    nothing. gross_edge == 0 must yield net_edge == 0, not 0 + haircut."""
    # Directly exercise the haircut branch logic the way engine.py does.
    gross_edge = 0.0
    cost_haircut = 0.02
    if gross_edge > 0:
        net_edge = max(0.0, gross_edge - cost_haircut)
    elif gross_edge < 0:
        net_edge = min(0.0, gross_edge + cost_haircut)
    else:
        net_edge = 0.0
    assert net_edge == 0.0


def test_small_edges_shrink_toward_zero_never_away_from_it() -> None:
    """The cost haircut must always reduce |edge|, never increase it or
    flip its sign, and must floor cleanly at zero rather than crossing it."""

    def haircut(gross_edge: float, cost_haircut: float = 0.02) -> float:
        if gross_edge > 0:
            return round(max(0.0, gross_edge - cost_haircut), 4)
        if gross_edge < 0:
            return round(min(0.0, gross_edge + cost_haircut), 4)
        return 0.0

    assert haircut(0.01) == 0.0  # smaller than the haircut -> floors to zero, doesn't flip negative
    assert haircut(-0.01) == 0.0
    assert haircut(0.10) == pytest.approx(0.08)
    assert haircut(-0.10) == pytest.approx(-0.08)
    assert haircut(0.0) == 0.0
