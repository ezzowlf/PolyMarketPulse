"""Explicit look-ahead-safety tests for the `as_of` parameter threaded
through history.py/engine.py/evidence.py (Proof-of-Edge backtest task).

Each test constructs a case where a FUTURE-dated item (a market resolution
or a news article published after the backtest's `as_of` cutoff) WOULD
change the result if it leaked into the computation, then proves `as_of`
correctly excludes it -- not just that the parameter exists and is passed
through, but that it actually changes behavior.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.prediction.history import _load_comparable_candidates

AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
FUTURE = datetime(2026, 6, 1, tzinfo=UTC)  # strictly after AS_OF
PAST = datetime(2025, 6, 1, tzinfo=UTC)  # strictly before AS_OF


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE markets (
            market_id TEXT PRIMARY KEY, provider TEXT, provider_market_id TEXT,
            condition_id TEXT, question TEXT, slug TEXT, category TEXT,
            classified_category TEXT, event_type TEXT, entities_json TEXT,
            proposition_json TEXT, start_date TEXT, end_date TEXT,
            url TEXT, first_seen_at TEXT, last_seen_at TEXT,
            resolution_status TEXT DEFAULT 'resolved'
        );
        CREATE TABLE market_resolutions (
            provider TEXT, provider_market_id TEXT, status TEXT, winning_outcome TEXT,
            resolved_at TEXT, detected_at TEXT
        );
        CREATE TABLE news_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, source TEXT,
            published_at TEXT, source_url TEXT, fetched_at TEXT
        );
        CREATE TABLE news_market_links (
            provider TEXT, provider_market_id TEXT, news_event_id INTEGER, confidence REAL
        );
        """
    )
    return c


def _seed_market(conn, pmid: str, question: str, category: str, resolved_at: datetime) -> None:
    now = "2025-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, condition_id, question, slug, "
        "category, classified_category, url, first_seen_at, last_seen_at) "
        "VALUES (?, 'polymarket', ?, '', ?, ?, ?, ?, 'https://x', ?, ?)",
        (pmid, pmid, question, pmid, category, category, now, now),
    )
    conn.execute(
        "INSERT INTO market_resolutions (provider, provider_market_id, status, winning_outcome, "
        "resolved_at, detected_at) VALUES ('polymarket', ?, 'resolved', 'Yes', ?, ?)",
        (pmid, resolved_at.isoformat(), resolved_at.isoformat()),
    )
    conn.commit()


def test_future_resolved_market_excluded_from_historical_comparables(conn) -> None:
    """A market that resolves AFTER `as_of` must not appear as a historical
    comparable candidate for a forecast made AT `as_of` -- including it
    would leak the future outcome into the backtest's base rate."""
    _seed_market(conn, "future-mkt", "Will the future event happen?", "POLITICS", FUTURE)
    _seed_market(conn, "past-mkt", "Did the past event happen?", "POLITICS", PAST)

    candidates_asof = _load_comparable_candidates(conn, provider="polymarket", as_of=AS_OF)
    ids_asof = {c.market_id for c in candidates_asof}
    assert "future-mkt" not in ids_asof, "future resolution leaked into as_of-restricted comparables"
    assert "past-mkt" in ids_asof

    # Without as_of (live/normal path), both are visible -- proves the
    # exclusion above is really caused by as_of, not some unrelated filter.
    candidates_live = _load_comparable_candidates(conn, provider="polymarket", as_of=None)
    ids_live = {c.market_id for c in candidates_live}
    assert "future-mkt" in ids_live
    assert "past-mkt" in ids_live


def test_future_published_news_excluded_from_independent_evidence(conn) -> None:
    """Two news articles published AFTER `as_of` must not be usable as
    independent evidence for a forecast made AT `as_of` -- if they leaked,
    the 2-item relevance-gate minimum (MIN_EVIDENCE_ITEMS_FOR_ESTIMATE) would
    be met and evidence scoring would proceed; with as_of correctly applied,
    zero items are visible and the result must report 0 found."""
    for i in range(2):
        conn.execute(
            "INSERT INTO news_events (title, source, published_at, source_url, fetched_at) "
            "VALUES (?, 'reuters', ?, ?, ?)",
            (f"Future-dated report #{i} confirming the outcome", FUTURE.isoformat(), f"https://x/{i}", FUTURE.isoformat()),
        )
        event_id = conn.execute("SELECT id FROM news_events WHERE source_url = ?", (f"https://x/{i}",)).fetchone()[0]
        conn.execute(
            "INSERT INTO news_market_links (provider, provider_market_id, news_event_id, confidence) "
            "VALUES ('polymarket', 'evtarget', ?, 0.9)",
            (event_id,),
        )
    conn.commit()

    result_asof = compute_independent_evidence(
        conn,
        provider="polymarket",
        provider_market_id="evtarget",
        question="Will the target event happen?",
        resolution_text=None,
        market_yes_price=None,
        now=AS_OF,
    )
    assert result_asof.available is False, (
        "future-published news leaked into as_of-restricted independent evidence"
    )
    assert "0 gefunden" in result_asof.detail, (
        f"expected 0 items visible under as_of, got detail={result_asof.detail!r}"
    )

    # Without the as_of cutoff (here: passing FUTURE explicitly as "now",
    # i.e. the forecast is made AFTER the articles were published), the same
    # two articles ARE visible -- proving the exclusion above is really the
    # now/as_of cutoff at work, not some unrelated filter.
    result_live = compute_independent_evidence(
        conn,
        provider="polymarket",
        provider_market_id="evtarget",
        question="Will the target event happen?",
        resolution_text=None,
        market_yes_price=None,
        now=FUTURE,
    )
    assert "0 gefunden" not in result_live.detail, (
        f"expected the 2 future articles to be visible when now=FUTURE, got detail={result_live.detail!r}"
    )
