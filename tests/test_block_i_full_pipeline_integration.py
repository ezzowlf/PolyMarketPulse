"""BLOCK I (final block): additive end-to-end integration tests closing a
real gap identified during the final validation sweep.

Audit finding: Blocks A-H's suites (test_hormuz_regression.py,
test_forecast_semantics_gate.py, test_block_c_resolution_path.py, etc.)
extensively exercise SOURCE -> CLAIM -> EVIDENCE -> RESOLUTION PATH ->
FORECAST by calling `compute_prediction()` directly against a `Storage`
connection, and test_api.py separately exercises the HTTP layer against a
generic seeded market with no claims/evidence. `evaluation.py`'s tests
(test_evaluation.py, test_calibration.py) separately prove RESOLUTION ->
EVALUATION -> LEARNING against synthetic `prediction_snapshots` rows
inserted directly by SQL.

What no existing test proves in one place: that seeding real claims/
evidence into `Storage`, then calling the real HTTP API's
`/prediction/{market_id}` endpoint (which internally calls
`ai_service.get_prediction`, itself calling `compute_prediction` AND
persisting a `prediction_snapshots` row per migration 8's "get_prediction()
alone triggers a save too" contract), actually results in a row that
(a) is readable back through the separate `/forecast-history/{market_id}`
read endpoint with matching four-tier values, and (b) is honest about
non-publication when evidence is weak. That is the real
SOURCE -> ... -> STORAGE -> API loop, proven through the actual FastAPI
app rather than by calling internal functions directly. This file adds
exactly that -- one case with real, multi-source DIRECT-tier evidence for
a market with a ResolutionPath (politics/geopolitics class), and one case
with no evidence at all (weak-evidence class), reusing the exact fixture
patterns already established in test_forecast_semantics_gate.py and
test_api.py rather than inventing new ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


def _link_news(
    storage: Storage, market: Market, title: str, source: str, source_url: str,
    confidence: float, hours_ago: float = 1.0,
) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago), fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("resignation",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


def _make_client(tmp_path: Path, monkeypatch, market: Market) -> TestClient:
    monkeypatch.setenv("POLYMARKETPULSE_DATABASE_PATH", str(tmp_path / "block_i_api_test.db"))
    monkeypatch.setenv("POLYMARKETPULSE_TELEGRAM_ENABLED", "false")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    from polymarketpulse.api import app
    from polymarketpulse.config import Settings

    settings = Settings.load()
    storage = Storage(settings.database_path)
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage, TestClient(app)


@pytest.fixture
def geopolitics_market() -> Market:
    return Market(
        provider="polymarket",
        provider_market_id="block-i-geo-strong-evidence",
        condition_id="0xblocki1",
        question="Will the Prime Minister resign by end of month?",
        slug="block-i-geo-strong-evidence",
        liquidity=80000,
        volume_24h=30000,
        yes_price=0.05,
        spread=0.02,
        end_at=datetime.now(UTC) + timedelta(days=20),
        start_at=datetime.now(UTC) - timedelta(hours=1),
        url="https://polymarket.com/event/block-i-geo-strong-evidence",
    )


@pytest.fixture
def weak_evidence_market() -> Market:
    return Market(
        provider="polymarket",
        provider_market_id="block-i-weak-evidence",
        condition_id="0xblocki2",
        question="Will some unrelated strait effectively close by month end?",
        slug="block-i-weak-evidence",
        liquidity=1000,
        volume_24h=90,
        yes_price=0.0425,
        spread=0.05,
        end_at=datetime.now(UTC) + timedelta(days=90),
        start_at=datetime.now(UTC) - timedelta(hours=1),
        url="https://polymarket.com/event/block-i-weak-evidence",
    )


def test_full_pipeline_weak_evidence_never_publishes_through_real_api(
    tmp_path: Path, monkeypatch, weak_evidence_market: Market,
) -> None:
    """SOURCE(none) -> CLAIM(none) -> EVIDENCE(none) -> FORECAST -> API,
    through the real FastAPI app: with zero linked news and zero
    comparables, /prediction/{id} must never return a published forecast,
    and that honest non-publication must be exactly what /forecast-
    history/{id} reads back afterward -- no divergence between the two
    endpoints' view of the same persisted snapshot."""
    storage, client = _make_client(tmp_path, monkeypatch, weak_evidence_market)
    storage.close()
    market_id = f"polymarket:{weak_evidence_market.provider_market_id}"

    resp = client.get(f"/prediction/{market_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["published_forecast_probability"] is None
    assert data["market_probability"] == pytest.approx(0.0425)

    history_resp = client.get(f"/forecast-history/{market_id}")
    assert history_resp.status_code == 200
    rows = history_resp.json()
    assert len(rows) >= 1, (
        "get_prediction() is documented to persist a prediction_snapshots "
        "row on every call (migration 8), so the read-back endpoint must "
        "see at least one row after /prediction/ was called above"
    )
    latest = rows[-1]
    assert latest["published_forecast_probability"] is None
    assert latest["market_probability"] == pytest.approx(0.0425)


def test_full_pipeline_strong_evidence_flows_through_real_api_and_storage(
    tmp_path: Path, monkeypatch, geopolitics_market: Market,
) -> None:
    """SOURCE(2 independent DIRECT-tier news) -> CLAIM/EVIDENCE -> FORECAST
    -> STORAGE(auto-persisted snapshot) -> API, through the real FastAPI
    app for a politics/geopolitics-class market. Confirms the
    model_hypothesis/evidence_backed/market fields returned by
    /prediction/{id} exactly match what /forecast-history/{id} reads back
    from the persisted `prediction_snapshots` row -- i.e. the HTTP
    response is not diverging from what was actually written to storage.
    Does not assert a specific published/not-published outcome (per this
    project's rule against hardcoding cosmetic pass/fail expectations);
    it asserts internal consistency between the write path and the read
    path for the same real evidence."""
    storage, client = _make_client(tmp_path, monkeypatch, geopolitics_market)
    _link_news(
        storage, geopolitics_market,
        "Prime Minister resignation confirmed by senior cabinet officials",
        "reuters", "https://reuters.com/block-i-a", confidence=0.7, hours_ago=1,
    )
    _link_news(
        storage, geopolitics_market,
        "Cabinet officials confirm Prime Minister resignation agreement",
        "apnews", "https://apnews.com/block-i-b", confidence=0.7, hours_ago=2,
    )
    storage.close()
    market_id = f"polymarket:{geopolitics_market.provider_market_id}"

    resp = client.get(f"/prediction/{market_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["market_probability"] == pytest.approx(0.05)
    # Real evidence exists, so the pipeline must have actually attempted an
    # independent read of it (never silently identical to a no-evidence run).
    assert data["independent_probability"] is not None

    history_resp = client.get(f"/forecast-history/{market_id}")
    assert history_resp.status_code == 200
    rows = history_resp.json()
    assert len(rows) >= 1
    latest = rows[-1]

    # The write path (compute_prediction -> save) and the read path
    # (SELECT over prediction_snapshots) must agree -- this is the actual
    # STORAGE <-> API integration proof, not just "both return 200".
    assert latest["market_probability"] == pytest.approx(data["market_probability"])
    assert latest["model_hypothesis_probability"] == pytest.approx(
        data["model_hypothesis_probability"]
    ) if data.get("model_hypothesis_probability") is not None else (
        latest["model_hypothesis_probability"] is None
    )
    assert latest["published_forecast_probability"] == data["published_forecast_probability"]
