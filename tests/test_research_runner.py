"""Real, mocked (no live network) tests for research_runner.run_research_for_market
— the actual end-to-end Source Fetch -> Claim Extraction -> Evidence ->
Forecast Recompute -> Storage wiring, plus its persisted Observability
record."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from polymarketpulse.config import Settings
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.research_runner import run_research_for_market
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _seed_market(storage: Storage) -> dict:
    question = "Will the ceasefire be confirmed by officials?"
    now = datetime.now(UTC).isoformat()
    storage.connection.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
        "first_seen_at, last_seen_at, resolution_status) "
        "VALUES ('rr-1', 'polymarket', 'rr-1', ?, 'rr-1', 'https://x', ?, ?, 'open')",
        (question, now, now),
    )
    storage.connection.commit()
    row = storage.connection.execute(
        "SELECT market_id, provider, provider_market_id, question, category, classified_category, resolution_source "
        "FROM markets WHERE provider_market_id = 'rr-1'"
    ).fetchone()
    cols = ("market_id", "provider", "provider_market_id", "question", "category", "classified_category", "resolution_source")
    return dict(zip(cols, row, strict=True))


def test_real_run_fetches_sources_extracts_claims_and_persists_observability(storage: Storage) -> None:
    market_row = _seed_market(storage)
    settings = Settings.load()

    fake_events = [
        NewsEvent(
            source="reuters", source_url="https://reuters.com/a", title="Ceasefire confirmed by officials",
            published_at=datetime.now(UTC), fetched_at=datetime.now(UTC),
        ),
        NewsEvent(
            source="apnews", source_url="https://apnews.com/b", title="Ceasefire confirmed by officials (AP)",
            published_at=datetime.now(UTC), fetched_at=datetime.now(UTC),
        ),
    ]
    with patch("polymarketpulse.news.gdelt.fetch_gdelt", return_value=fake_events):
        record = run_research_for_market(storage, settings, market_row, trigger="test")

    assert record.sources_requested == 1
    assert record.sources_fetched == 2
    assert record.sources_accepted >= 1  # real linker decides exact match count
    assert record.claims_extracted >= 1  # real claim extraction, not a placeholder
    assert record.final_status  # forecast recompute actually ran

    # Persisted, retrievable Observability record — not just a log line.
    rows = storage.get_research_runs(provider_market_id="rr-1")
    assert len(rows) == 1
    assert rows[0]["sources_fetched"] == 2
    assert rows[0]["claims_extracted"] == record.claims_extracted


def test_no_sources_found_still_completes_and_reports_zero_honestly(storage: Storage) -> None:
    market_row = _seed_market(storage)
    settings = Settings.load()

    with patch("polymarketpulse.news.gdelt.fetch_gdelt", return_value=[]):
        record = run_research_for_market(storage, settings, market_row, trigger="test")

    assert record.sources_fetched == 0
    assert record.sources_accepted == 0
    assert record.claims_extracted == 0  # honestly zero, not fabricated
