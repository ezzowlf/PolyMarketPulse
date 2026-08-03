from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market, ResolutionStatus
from polymarketpulse.signals import generate_signals
from polymarketpulse.stats import compute_signal_stats
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _market(**overrides) -> Market:
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "",
        "question": "Test",
        "slug": "test",
        "liquidity": 50000,
        "volume_24h": 20000,
        "yes_price": 0.7,
        "start_at": datetime.now(UTC) - timedelta(hours=1),
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_stats_without_evaluations_returns_none_scores(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    stats = compute_signal_stats(storage.connection)
    assert stats.signal_count >= 1
    assert stats.evaluated_count == 0
    assert stats.hit_rate is None
    assert stats.brier_score is None


def test_brier_score_only_computed_with_forecast_probability(storage: Storage) -> None:
    market = _market(yes_price=0.8)
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])

    resolved = _market(
        yes_price=1.0,
        resolution_status=ResolutionStatus.RESOLVED,
        winning_outcome="Yes",
        resolved_at=datetime.now(UTC),
    )
    storage.record_resolution(resolved)

    stats = compute_signal_stats(storage.connection)
    # No forecast_probability was ever set on these signals, so Brier/log
    # loss must stay unset even though evaluations exist.
    assert stats.evaluated_count >= 1
    assert stats.brier_score is None
    assert stats.log_loss is None
    assert stats.hit_rate is not None
