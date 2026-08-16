"""Phase H tests — confirms extract_event()'s output, computed during real
evidence scoring, is persisted into the migration-12/15 `events` table with
the right fields and provenance, and that this additive persistence hook
does not change compute_independent_evidence's own output/behavior."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _link_news(storage: Storage, market: Market, title: str, source: str, source_url: str, hours_ago: float, confidence: float = 0.6) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("ceasefire",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


def _market() -> Market:
    return Market(
        provider="polymarket", provider_market_id="evidence-h-1", condition_id="",
        question="Will the ceasefire agreement be confirmed?", slug="evidence-h-1",
    )


def test_schema_version_is_18(storage: Storage) -> None:
    assert storage.schema_version() == 34


def test_events_table_has_phase_h_columns(storage: Storage) -> None:
    cols = {row[1] for row in storage.connection.execute("PRAGMA table_info(events)").fetchall()}
    for expected in ("actors_json", "action", "target", "expected_time", "status",
                      "source_type", "certainty", "provider", "provider_market_id", "news_event_id"):
        assert expected in cols


def test_real_evidence_scoring_persists_extracted_events(storage: Storage) -> None:
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", "https://reuters.com/a", hours_ago=1)
    _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", "https://apnews.com/b", hours_ago=3)

    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-h-1",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is True  # sanity: this is a real, active scoring path

    rows = storage.connection.execute(
        "SELECT title, event_type, action, status, certainty, provider, provider_market_id, source, news_event_id "
        "FROM events WHERE provider = ? AND provider_market_id = ?",
        ("polymarket", "evidence-h-1"),
    ).fetchall()
    assert len(rows) == 2
    titles = {r[0] for r in rows}
    assert "Ceasefire confirmed by both sides, agreement signed" in titles
    for title, event_type, action, status, certainty, provider, provider_market_id, source, news_event_id in rows:
        assert event_type == "ceasefire"
        assert action == "deescalation"
        assert status == "actual"
        assert certainty in ("confirmed", "unknown")  # depends on exact wording ("confirmed" vs "confirm")
        assert provider == "polymarket"
        assert provider_market_id == "evidence-h-1"
        assert source in ("reuters", "apnews")
        assert news_event_id is not None
    by_title = {r[0]: r[4] for r in rows}
    assert by_title["Ceasefire confirmed by both sides, agreement signed"] == "confirmed"


def test_persistence_does_not_change_evidence_scoring_output(storage: Storage) -> None:
    """Same scenario computed twice (a second time against a DB where the
    `events` table has already been populated by the first run) must
    produce an identical result — persistence is a pure side effect."""
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", "https://reuters.com/a", hours_ago=1)
    _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", "https://apnews.com/b", hours_ago=3)

    result_1 = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-h-1",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    result_2 = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-h-1",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    assert result_1.independent_yes_probability == result_2.independent_yes_probability
    assert result_1.confirmation_count == result_2.confirmation_count
    assert result_1.available == result_2.available

    # Running it twice persists twice (additive, no dedup logic claimed) —
    # confirms the hook fires on every real scoring call, not just once.
    count = storage.connection.execute(
        "SELECT COUNT(*) FROM events WHERE provider = ? AND provider_market_id = ?",
        ("polymarket", "evidence-h-1"),
    ).fetchone()[0]
    assert count == 4


def test_persistence_failure_is_non_fatal(storage: Storage) -> None:
    """If the events table doesn't have the Phase H columns (e.g. an older
    DB), evidence scoring must still succeed rather than raising."""
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", "https://reuters.com/a", hours_ago=1)
    _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", "https://apnews.com/b", hours_ago=3)

    # Simulate a pre-migration-15 events table by dropping the new columns'
    # backing table and recreating a minimal legacy-shaped one.
    storage.connection.execute("DROP TABLE events")
    storage.connection.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT,
            geographic_scope TEXT,
            source TEXT,
            source_url TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    storage.connection.commit()

    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-h-1",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is True
