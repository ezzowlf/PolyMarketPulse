"""Phase N: tests for the shadow forecast snapshot persistence path
(prediction_snapshots table, migration 16) and its critical no-look-ahead
invariant — this write path must never read or use resolution/outcome
data, even when the market has already resolved by the time the snapshot
is written.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from polymarketpulse.ai.service import get_prediction
from polymarketpulse.models import Market, ResolutionStatus
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _market(pmid: str, question: str, yes_price: float) -> Market:
    return Market(
        provider="polymarket", provider_market_id=pmid, condition_id="", question=question,
        slug=f"m-{pmid}", category="geopolitics", yes_price=yes_price, no_price=1 - yes_price,
    )


def test_prediction_snapshot_schema_has_no_resolution_outcome_column(storage: Storage) -> None:
    """Schema-level guard: the prediction_snapshots table must never grow a
    column that stores the resolution outcome directly — resolution join
    happens later, read-only, in calibration.py, never at write time."""
    cols = {row[1] for row in storage.connection.execute("PRAGMA table_info(prediction_snapshots)")}
    forbidden = {"winning_outcome", "final_outcome", "resolved_outcome", "outcome", "resolution_status"}
    assert cols & forbidden == set()
    # And the new Phase N columns are present.
    expected_new = {
        "forecast_at", "market_probability_at_forecast", "blended_probability",
        "calibrated_probability", "confidence_calibration_status", "forecast_status",
        "models_used", "divergence_verdict",
    }
    assert expected_new <= cols


def test_snapshot_written_after_resolution_reflects_only_forecast_time_data(storage: Storage) -> None:
    """The exact scenario the spec calls out: create a market, resolve it
    (record_resolution), THEN request a prediction/snapshot for it. The
    stored `market_probability_at_forecast` must be exactly the market
    price supplied at forecast-call time (via the market_snapshots table),
    NOT anything derived from the resolution — and there must be no
    resolution-outcome field on the row at all.
    """
    pmid = "already-resolved-1"
    market = _market(pmid, f"Will {pmid} happen?", yes_price=0.37)

    # 1. Market exists with a forecast-time price of 0.37.
    storage.save(run_id=storage.start_run("polymarket"), market_signals=[(market, [])])

    # 2. Market resolves YES at price 1.0 — this must NOT leak into the
    #    snapshot written in step 3.
    resolved_market = Market(
        provider="polymarket", provider_market_id=pmid, condition_id="", question=market.question,
        slug=market.slug, category=market.category, resolution_status=ResolutionStatus.RESOLVED,
        winning_outcome="Yes", resolved_at=datetime.now(UTC), yes_price=1.0, no_price=0.0,
    )
    recorded = storage.record_resolution(resolved_market)
    assert recorded is True

    # 3. Request a prediction/snapshot AFTER resolution. The write path
    #    (get_prediction -> _persist_prediction_snapshot) only ever reads
    #    `market_snapshots` (forecast-time price) and never touches
    #    `market_resolutions` at all.
    prediction = get_prediction(storage, f"polymarket:{pmid}")
    assert prediction is not None

    row = storage.connection.execute(
        "SELECT market_probability_at_forecast, market_yes_probability, forecast_at "
        "FROM prediction_snapshots WHERE provider_market_id = ? ORDER BY id DESC LIMIT 1",
        (pmid,),
    ).fetchone()
    assert row is not None
    market_probability_at_forecast, market_yes_probability, forecast_at = row

    # Reflects the forecast-time price (0.37), never the resolution price (1.0).
    assert market_probability_at_forecast == pytest.approx(0.37)
    assert market_yes_probability == pytest.approx(0.37)
    assert market_probability_at_forecast != 1.0
    assert forecast_at is not None

    # No resolution-outcome column exists on the row at all (schema check,
    # belt-and-suspenders alongside the dedicated schema test above).
    cols = {d[0] for d in storage.connection.execute("SELECT * FROM prediction_snapshots LIMIT 0").description}
    assert "winning_outcome" not in cols
    assert "final_outcome" not in cols


def test_snapshot_captures_forecast_status_and_calibration_status(storage: Storage) -> None:
    pmid = "fresh-market-1"
    market = _market(pmid, f"Will {pmid} happen?", yes_price=0.55)
    storage.save(run_id=storage.start_run("polymarket"), market_signals=[(market, [])])

    get_prediction(storage, f"polymarket:{pmid}")

    row = storage.connection.execute(
        "SELECT forecast_status, confidence_calibration_status, engine_version "
        "FROM prediction_snapshots WHERE provider_market_id = ? ORDER BY id DESC LIMIT 1",
        (pmid,),
    ).fetchone()
    assert row is not None
    forecast_status, confidence_calibration_status, engine_version = row
    assert forecast_status is not None
    assert confidence_calibration_status == "UNCALIBRATED"
    assert engine_version is not None
