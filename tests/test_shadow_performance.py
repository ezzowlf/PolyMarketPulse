import json
import sqlite3
from datetime import UTC, datetime

import pytest

from polymarketpulse.migrations import run_migrations
from polymarketpulse.shadow_performance import (
    compute_shadow_performance,
    compute_submodel_comparison,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    run_migrations(c)
    yield c
    c.close()


def _insert_market(conn, market_id="m1"):
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
        "first_seen_at, last_seen_at, resolution_status) VALUES (?, 'polymarket', ?, 'Q?', 's', 'u', ?, ?, 'unresolved')",
        (market_id, market_id, now, now),
    )
    conn.commit()


def _insert_shadow_trade(conn, **fields):
    defaults = {
        "market_id": "m1", "provider": "polymarket", "provider_market_id": "m1", "source_snapshot_id": None,
        "created_at": datetime.now(UTC).isoformat(), "direction": "YES", "entry_market_price": 0.5,
        "independent_probability": 0.65, "expected_edge": 0.13, "confidence": 70.0, "opportunity_score": 65.0,
        "reliability_score": 70.0, "manipulation_risk": 10.0, "deadline_phase": "MORE_THAN_7_DAYS",
        "assumed_stake": 1.0, "simulated_fee": 0.02, "simulated_slippage": 0.005,
        "reasons_json": "[]", "blockers_json": "[]", "status": "closed",
        "max_drawdown": 0.05, "simulated_pnl": 0.3, "roi": 0.3, "holding_hours": 48.0,
        "final_outcome": "YES", "closed_at": datetime.now(UTC).isoformat(),
    }
    defaults.update(fields)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(f"INSERT INTO shadow_trades ({cols}) VALUES ({placeholders})", tuple(defaults.values()))
    conn.commit()


def test_empty_history_returns_neutral_report(conn) -> None:
    report = compute_shadow_performance(conn)
    assert report.n_closed == 0
    assert report.hit_rate is None


def test_winning_trade_counted_in_hit_rate(conn) -> None:
    _insert_market(conn)
    _insert_shadow_trade(conn, direction="YES", final_outcome="YES", simulated_pnl=0.3, roi=0.3)
    report = compute_shadow_performance(conn)
    assert report.n_closed == 1
    assert report.hit_rate == 1.0
    assert report.total_pnl == 0.3


def test_losing_trade_lowers_hit_rate(conn) -> None:
    _insert_market(conn)
    _insert_shadow_trade(conn, direction="YES", final_outcome="NO", simulated_pnl=-0.5, roi=-0.5)
    report = compute_shadow_performance(conn)
    assert report.hit_rate == 0.0
    assert report.total_pnl == -0.5


def test_skipped_blockers_are_counted(conn) -> None:
    _insert_market(conn)
    _insert_shadow_trade(conn, status="skipped", final_outcome=None, blockers_json=json.dumps(["Edge unter Mindestschwelle"]), simulated_pnl=None, roi=None)
    _insert_shadow_trade(conn, status="skipped", final_outcome=None, blockers_json=json.dumps(["Edge unter Mindestschwelle"]), simulated_pnl=None, roi=None)
    report = compute_shadow_performance(conn)
    assert report.n_skipped == 2
    assert report.most_common_blockers[0]["blocker"] == "Edge unter Mindestschwelle"
    assert report.most_common_blockers[0]["count"] == 2


def test_equity_curve_is_cumulative(conn) -> None:
    _insert_market(conn)
    _insert_shadow_trade(conn, simulated_pnl=0.2, roi=0.2, closed_at="2026-01-01T00:00:00+00:00")
    _insert_shadow_trade(conn, simulated_pnl=-0.1, roi=-0.1, closed_at="2026-01-02T00:00:00+00:00")
    report = compute_shadow_performance(conn)
    assert report.equity_curve[0]["cumulative_pnl"] == 0.2
    assert report.equity_curve[1]["cumulative_pnl"] == pytest.approx(0.1)


def test_submodel_comparison_empty_without_resolutions(conn) -> None:
    entries = compute_submodel_comparison(conn)
    assert entries == []


def test_submodel_comparison_scores_each_submodel(conn) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO prediction_snapshots (market_id, provider, provider_market_id, category, prediction_version, "
        "created_at, market_yes_probability, estimated_yes_probability, net_yes_edge, confidence_score, "
        "recommendation, comparable_sample_size, submodel_estimates_json) VALUES "
        "('m1', 'polymarket', '1', NULL, 'v2', ?, 0.5, 0.7, 0.2, 70, 'YES', 10, ?)",
        (now, json.dumps([{"name": "momentum", "estimated_yes_probability": 0.7, "weight": 0.4, "available": True}])),
    )
    conn.execute(
        "INSERT INTO market_resolutions (provider, provider_market_id, resolved_at, winning_outcome, status, detected_at) "
        "VALUES ('polymarket', '1', ?, 'Yes', 'resolved', ?)",
        (now, now),
    )
    conn.commit()
    entries = compute_submodel_comparison(conn)
    assert len(entries) == 1
    assert entries[0].name == "momentum"
    assert entries[0].hit_rate == 1.0
