import sqlite3

from polymarketpulse.migrations import current_schema_version, run_migrations


def test_run_migrations_on_fresh_db_applies_all() -> None:
    conn = sqlite3.connect(":memory:")
    applied = run_migrations(conn)
    assert applied == list(range(1, 27))
    assert current_schema_version(conn) == 26


def test_run_migrations_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    second_run = run_migrations(conn)
    assert second_run == []
    assert current_schema_version(conn) == 26


def test_migration_preserves_existing_phase1_data() -> None:
    conn = sqlite3.connect(":memory:")
    # Simulate a pre-Phase-2 database with just the original tables/data.
    conn.executescript(
        """
        CREATE TABLE scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
            finished_at TEXT, markets_fetched INTEGER NOT NULL DEFAULT 0,
            signals_saved INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE markets (
            market_id TEXT PRIMARY KEY, condition_id TEXT, question TEXT NOT NULL,
            slug TEXT NOT NULL, category TEXT, tags TEXT, url TEXT NOT NULL,
            yes_token_id TEXT, no_token_id TEXT, start_date TEXT, end_date TEXT,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, last_alerted_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO markets (market_id, question, slug, url, first_seen_at, last_seen_at) "
        "VALUES ('123', 'Old market', 'old-market', 'https://x', '2026-01-01', '2026-01-01')"
    )
    conn.commit()

    run_migrations(conn)

    row = conn.execute(
        "SELECT question, provider, provider_market_id, resolution_status FROM markets WHERE market_id = '123'"
    ).fetchone()
    assert row[0] == "Old market"
    assert row[1] == "polymarket"
    assert row[2] == "123"
    assert row[3] == "unresolved"


def test_migration_creates_new_tables() -> None:
    conn = sqlite3.connect(":memory:")
    run_migrations(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for expected in (
        "providers",
        "market_outcomes",
        "orderbook_snapshots",
        "research_signals",
        "market_resolutions",
        "signal_evaluations",
        "news_events",
        "news_market_links",
        "market_matches",
        "analysis_runs",
        "model_metrics",
        "watchlist_items",
        "data_quality_reports",
        "news_market_reactions",
        "ai_analysis_runs",
        "shadow_setups",
        "public_trade_events",
        "public_wallet_positions",
        "wallet_market_statistics",
        "market_flow_signals",
        "market_reliability_snapshots",
        "manipulation_risk_events",
        "shadow_trades",
        "entities",
        "entity_aliases",
        "events",
        "event_entity_links",
        "event_market_relevance",
        "event_relations",
        "provider_health",
        "claims",
        "claim_groups",
        "claim_sources",
        "claim_counter_evidence",
    ):
        assert expected in tables
