from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotent ALTER TABLE ADD COLUMN (SQLite has no IF NOT EXISTS for this)."""
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _migration_001_initial(conn: sqlite3.Connection) -> None:
    """Baseline Phase-1 schema. CREATE TABLE IF NOT EXISTS everywhere so this
    is a no-op against a database that already has these tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            markets_fetched INTEGER NOT NULL DEFAULT 0,
            signals_saved INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS markets (
            market_id TEXT PRIMARY KEY,
            condition_id TEXT,
            question TEXT NOT NULL,
            slug TEXT NOT NULL,
            category TEXT,
            tags TEXT,
            url TEXT NOT NULL,
            yes_token_id TEXT,
            no_token_id TEXT,
            start_date TEXT,
            end_date TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_alerted_at TEXT
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES scanner_runs(id),
            captured_at TEXT NOT NULL,
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            yes_price REAL,
            no_price REAL,
            best_bid REAL,
            best_ask REAL,
            liquidity REAL NOT NULL,
            volume_24h REAL NOT NULL,
            volume_total REAL NOT NULL,
            spread REAL,
            one_day_change REAL,
            opportunity_score REAL NOT NULL,
            reasons TEXT NOT NULL,
            UNIQUE(market_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_market_time
        ON market_snapshots(market_id, captured_at);

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            captured_at TEXT NOT NULL,
            yes_price REAL,
            no_price REAL,
            UNIQUE(market_id, captured_at)
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES scanner_runs(id),
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            captured_at TEXT NOT NULL,
            opportunity_score REAL NOT NULL,
            reasons TEXT NOT NULL,
            UNIQUE(market_id, run_id)
        );

        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL REFERENCES signals(id),
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            resolved_at TEXT,
            outcome TEXT,
            notes TEXT
        );
        """
    )


def _migration_002_provider_architecture(conn: sqlite3.Connection) -> None:
    """Extends the schema for multi-provider support, resolutions, richer
    research signals, news, and cross-provider matching. Existing rows are
    preserved; `provider` on pre-existing rows defaults to 'polymarket'
    since that was the only provider before this migration.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS providers (
            name TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            real_money INTEGER NOT NULL DEFAULT 1,
            requires_auth INTEGER NOT NULL DEFAULT 0,
            capabilities_json TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        """
    )

    # --- markets: add provider-aware columns ---
    _add_column(conn, "markets", "provider", "TEXT")
    _add_column(conn, "markets", "provider_market_id", "TEXT")
    _add_column(conn, "markets", "event_id", "TEXT")
    _add_column(conn, "markets", "description", "TEXT")
    _add_column(conn, "markets", "outcomes", "TEXT")
    _add_column(conn, "markets", "outcome_prices", "TEXT")
    _add_column(conn, "markets", "resolved_at", "TEXT")
    _add_column(conn, "markets", "resolution_status", "TEXT")
    _add_column(conn, "markets", "winning_outcome", "TEXT")
    _add_column(conn, "markets", "resolution_source", "TEXT")
    _add_column(conn, "markets", "raw_data_hash", "TEXT")
    _add_column(conn, "markets", "provider_data", "TEXT")
    conn.execute(
        "UPDATE markets SET provider = 'polymarket' WHERE provider IS NULL",
    )
    conn.execute(
        "UPDATE markets SET provider_market_id = market_id WHERE provider_market_id IS NULL",
    )
    conn.execute(
        "UPDATE markets SET resolution_status = 'unresolved' WHERE resolution_status IS NULL",
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_markets_provider_native "
        "ON markets(provider, provider_market_id)"
    )

    # --- scanner_runs: provider + richer run bookkeeping ---
    _add_column(conn, "scanner_runs", "provider", "TEXT")
    _add_column(conn, "scanner_runs", "status", "TEXT")
    _add_column(conn, "scanner_runs", "markets_read", "INTEGER")
    _add_column(conn, "scanner_runs", "markets_saved", "INTEGER")
    _add_column(conn, "scanner_runs", "markets_failed", "INTEGER")
    _add_column(conn, "scanner_runs", "duration_ms", "INTEGER")
    _add_column(conn, "scanner_runs", "error_details", "TEXT")
    conn.execute("UPDATE scanner_runs SET provider = 'polymarket' WHERE provider IS NULL")
    conn.execute("UPDATE scanner_runs SET status = 'completed' WHERE status IS NULL")

    # --- market_snapshots: provider + change detection ---
    _add_column(conn, "market_snapshots", "provider", "TEXT")
    _add_column(conn, "market_snapshots", "is_heartbeat", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE market_snapshots SET provider = 'polymarket' WHERE provider IS NULL")

    # --- price_history: provider ---
    _add_column(conn, "price_history", "provider", "TEXT")
    conn.execute("UPDATE price_history SET provider = 'polymarket' WHERE provider IS NULL")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            outcome_index INTEGER NOT NULL,
            outcome_name TEXT NOT NULL,
            token_id TEXT,
            UNIQUE(provider, provider_market_id, outcome_index)
        );

        CREATE TABLE IF NOT EXISTS orderbook_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            bids_json TEXT NOT NULL,
            asks_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_orderbook_market_time
        ON orderbook_snapshots(provider, provider_market_id, captured_at);

        CREATE TABLE IF NOT EXISTS research_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES scanner_runs(id),
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            score REAL NOT NULL,
            reasons TEXT NOT NULL,
            subfactors_json TEXT NOT NULL,
            origin_yes_price REAL,
            origin_liquidity REAL,
            origin_days_to_resolution REAL,
            forecast_probability REAL,
            data_quality_flag INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE INDEX IF NOT EXISTS idx_research_signals_market
        ON research_signals(provider, provider_market_id, captured_at);

        CREATE TABLE IF NOT EXISTS market_resolutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            resolved_at TEXT NOT NULL,
            winning_outcome TEXT,
            final_yes_price REAL,
            final_no_price REAL,
            resolution_source TEXT,
            status TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            UNIQUE(provider, provider_market_id)
        );

        CREATE TABLE IF NOT EXISTS signal_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL REFERENCES research_signals(id),
            resolution_id INTEGER NOT NULL REFERENCES market_resolutions(id),
            origin_yes_price REAL,
            final_outcome TEXT,
            correct INTEGER,
            simulated_pnl_per_unit REAL,
            hold_duration_hours REAL,
            max_favorable_excursion REAL,
            max_adverse_excursion REAL,
            evaluated_at TEXT NOT NULL,
            UNIQUE(signal_id)
        );

        CREATE TABLE IF NOT EXISTS news_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            entities_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_news_events_hash ON news_events(content_hash);

        CREATE TABLE IF NOT EXISTS news_market_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_event_id INTEGER NOT NULL REFERENCES news_events(id),
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            match_reason TEXT NOT NULL,
            matched_terms TEXT NOT NULL,
            confidence REAL NOT NULL,
            confirmed TEXT NOT NULL DEFAULT 'automatic',
            created_at TEXT NOT NULL,
            UNIQUE(news_event_id, provider, provider_market_id)
        );

        CREATE TABLE IF NOT EXISTS market_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_a TEXT NOT NULL,
            provider_market_id_a TEXT NOT NULL,
            provider_b TEXT NOT NULL,
            provider_market_id_b TEXT NOT NULL,
            text_similarity REAL,
            date_similarity REAL,
            outcome_structure_match INTEGER,
            category_match INTEGER,
            status TEXT NOT NULL DEFAULT 'candidate',
            created_at TEXT NOT NULL,
            confirmed_at TEXT,
            UNIQUE(provider_a, provider_market_id_a, provider_b, provider_market_id_b)
        );

        CREATE TABLE IF NOT EXISTS analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            kind TEXT NOT NULL,
            parameters_json TEXT,
            status TEXT NOT NULL DEFAULT 'completed'
        );

        CREATE TABLE IF NOT EXISTS model_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_run_id INTEGER REFERENCES analysis_runs(id),
            scope TEXT NOT NULL,
            scope_value TEXT,
            metric_name TEXT NOT NULL,
            metric_value REAL,
            sample_size INTEGER,
            computed_at TEXT NOT NULL
        );
        """
    )


def _migration_003_watchlist(conn: sqlite3.Connection) -> None:
    """Adds the watchlist table backing the Phase-3 dashboard. Purely
    additive; no existing data touched."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS watchlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            note TEXT,
            alert_rules_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(provider, provider_market_id)
        );
        """
    )


def _migration_004_intelligence_platform(conn: sqlite3.Connection) -> None:
    """Phase 4: data-quality reports, news reaction tracking, watchlist
    enrichment (tags/rating/virtual positions), and search support.
    Purely additive; no existing data touched."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS data_quality_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES scanner_runs(id),
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            score REAL NOT NULL,
            issues_json TEXT NOT NULL,
            checks_passed_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dq_reports_market
        ON data_quality_reports(provider, provider_market_id, captured_at);

        CREATE TABLE IF NOT EXISTS news_market_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_market_link_id INTEGER NOT NULL REFERENCES news_market_links(id),
            price_before REAL,
            price_after REAL,
            price_change REAL,
            window_hours REAL NOT NULL,
            evaluated_at TEXT NOT NULL,
            UNIQUE(news_market_link_id)
        );
        """
    )
    _add_column(conn, "watchlist_items", "tags", "TEXT")
    _add_column(conn, "watchlist_items", "rating", "INTEGER")
    _add_column(conn, "watchlist_items", "group_name", "TEXT")
    _add_column(conn, "watchlist_items", "virtual_position_json", "TEXT")


def _migration_005_ai_analysis(conn: sqlite3.Connection) -> None:
    """Phase 5: AI analysis run log, doubling as the cache store (lookup by
    analysis_type + market_id + model + prompt_version + context_hash within
    a TTL, see ai/cache.py). Deliberately excludes API keys and raw prompt
    text — only the structured JSON response and bookkeeping metadata."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_analysis_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_type TEXT NOT NULL,
            market_id TEXT,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            duration_ms INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cached INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            response_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_runs_cache_lookup
        ON ai_analysis_runs(analysis_type, model, prompt_version, context_hash, created_at);
        CREATE INDEX IF NOT EXISTS idx_ai_runs_market
        ON ai_analysis_runs(market_id, created_at);
        """
    )


def _migration_006_shadow_setups(conn: sqlite3.Connection) -> None:
    """Phase 6: permanent Shadow-Setup history. A Shadow-Setup is created
    only when several independent factors align (see shadow.py); it is
    never deleted, so it becomes a growing knowledge base of what actually
    turned out to be useful research."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS shadow_setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES scanner_runs(id),
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            score REAL NOT NULL,
            breakdown_json TEXT NOT NULL,
            warum_interessant_json TEXT NOT NULL,
            warum_nicht_json TEXT NOT NULL,
            was_fehlt_json TEXT NOT NULL,
            confirming_factor_count INTEGER NOT NULL,
            origin_yes_price REAL,
            status TEXT NOT NULL DEFAULT 'aktiv',
            resolved_at TEXT,
            final_outcome TEXT,
            final_yes_price REAL,
            duration_hours REAL,
            useful_factors_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_setups_market
        ON shadow_setups(provider, provider_market_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_shadow_setups_status
        ON shadow_setups(status, created_at);
        """
    )


MIGRATIONS: list[Migration] = [
    (1, "initial_schema", _migration_001_initial),
    (2, "provider_architecture", _migration_002_provider_architecture),
    (3, "watchlist", _migration_003_watchlist),
    (4, "intelligence_platform", _migration_004_intelligence_platform),
    (5, "ai_analysis", _migration_005_ai_analysis),
    (6, "shadow_setups", _migration_006_shadow_setups),
]


def current_schema_version(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "schema_migrations"):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply all pending migrations in order. Idempotent: re-running against
    an already-migrated database applies nothing. Never drops or truncates
    existing tables/columns."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()

    applied_versions = {
        row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    newly_applied: list[int] = []
    for version, name, func in MIGRATIONS:
        if version in applied_versions:
            continue
        func(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        newly_applied.append(version)
    return newly_applied
