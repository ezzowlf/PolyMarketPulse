from datetime import UTC, datetime
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.shadow_trading import ShadowDecision
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _seed_market(storage: Storage) -> str:
    market = Market(
        provider="polymarket", provider_market_id="1", condition_id="", question="Will X happen?",
        slug="m-1", category="geopolitics", liquidity=50000, volume_24h=1000, yes_price=0.5,
        start_at=datetime.now(UTC),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = '1'"
    ).fetchone()[0]


def _decision(status="candidate", direction="YES") -> ShadowDecision:
    return ShadowDecision(
        market_id="x", provider="polymarket", provider_market_id="1", direction=direction, status=status,
        reasons=("edge ok",), blockers=(), entry_market_price=0.5, independent_probability=0.65,
        expected_edge=0.13, confidence=70.0, opportunity_score=65.0, reliability_score=70.0,
        manipulation_risk=10.0, deadline_phase="MORE_THAN_7_DAYS", assumed_stake=1.0,
        simulated_fee=0.02, simulated_slippage=0.005,
    )


def test_save_and_retrieve_candidate_shadow_trade(storage: Storage) -> None:
    market_id = _seed_market(storage)
    trade_id = storage.save_shadow_trade(_decision(), market_id, None, "v2")
    active = storage.active_shadow_trades()
    assert len(active) == 1
    assert active[0]["id"] == trade_id
    assert active[0]["status"] == "candidate"


def test_skipped_decision_is_persisted_with_blockers(storage: Storage) -> None:
    market_id = _seed_market(storage)
    decision = ShadowDecision(
        market_id="x", provider="polymarket", provider_market_id="1", direction="NONE", status="skipped",
        reasons=(), blockers=("Edge unter Mindestschwelle",), entry_market_price=0.5,
        independent_probability=None, expected_edge=0.01, confidence=70.0, opportunity_score=65.0,
        reliability_score=70.0, manipulation_risk=10.0, deadline_phase="MORE_THAN_7_DAYS",
        assumed_stake=1.0, simulated_fee=0.02, simulated_slippage=0.005,
    )
    storage.save_shadow_trade(decision, market_id, None, "v2")
    all_trades = storage.all_shadow_trades()
    assert all_trades[0]["status"] == "skipped"
    import json
    assert "Edge unter Mindestschwelle" in json.loads(all_trades[0]["blockers_json"])
    # skipped trades must not show up as active/candidate
    assert storage.active_shadow_trades() == []


def test_activate_and_lifecycle_update(storage: Storage) -> None:
    market_id = _seed_market(storage)
    trade_id = storage.save_shadow_trade(_decision(), market_id, None, "v2")
    storage.activate_shadow_trade(trade_id)
    storage.update_shadow_trade_lifecycle(trade_id, {"max_favorable_move": 0.2, "max_drawdown": 0.05})
    active = storage.active_shadow_trades()
    assert active[0]["status"] == "active"
    assert active[0]["max_favorable_move"] == 0.2


def test_close_shadow_trade_persists_outcome(storage: Storage) -> None:
    market_id = _seed_market(storage)
    trade_id = storage.save_shadow_trade(_decision(), market_id, None, "v2")
    storage.activate_shadow_trade(trade_id)
    storage.close_shadow_trade(trade_id, "resolved", "YES", 0.3, 0.3, 48.0, "Resolution")
    all_trades = storage.all_shadow_trades()
    closed = all_trades[0]
    assert closed["status"] == "closed"
    assert closed["final_outcome"] == "YES"
    assert closed["simulated_pnl"] == 0.3
    assert storage.active_shadow_trades() == []


def test_restart_preserves_shadow_trades(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    s1 = Storage(db_path)
    market_id = _seed_market(s1)
    trade_id = s1.save_shadow_trade(_decision(), market_id, None, "v2")
    s1.close()

    s2 = Storage(db_path)
    active = s2.active_shadow_trades()
    assert any(t["id"] == trade_id for t in active)
    s2.close()


def test_prediction_snapshot_stores_new_shadow_fields(storage: Storage) -> None:
    snapshot_id = storage.save_prediction_snapshot(
        market_id="m1", provider="polymarket", provider_market_id="1", category="geopolitics",
        prediction_version="v2", market_yes_probability=0.5, estimated_yes_probability=0.65,
        net_yes_edge=0.13, confidence_score=70.0, recommendation="YES", comparable_sample_size=10,
        independent_probability=0.65, resolution_clarity=75.0, market_reliability_score=70.0,
        market_reliability_level="hoch", manipulation_risk_score=10.0, opportunity_score=65.0,
        deadline_phase="MORE_THAN_7_DAYS", evidence_count=3, independent_confirmation_count=2,
        contradiction_present=False, engine_version="v2", config_hash="abc123",
    )
    row = storage.connection.execute(
        "SELECT independent_probability, market_reliability_level, engine_version, config_hash, contradiction_present "
        "FROM prediction_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    assert row[0] == 0.65
    assert row[1] == "hoch"
    assert row[2] == "v2"
    assert row[3] == "abc123"
    assert row[4] == 0
