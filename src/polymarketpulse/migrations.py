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


def _migration_007_ai_cost_tracking(conn: sqlite3.Connection) -> None:
    """Phase 7: adds token-cost bookkeeping to ai_analysis_runs (cached
    input tokens, estimated vs. actual USD cost) and a market_id-agnostic
    daily spend view is computed on the fly from these columns — no
    hardcoded prices are stored, only computed costs per run."""
    _add_column(conn, "ai_analysis_runs", "cached_input_tokens", "INTEGER")
    _add_column(conn, "ai_analysis_runs", "estimated_cost_usd", "REAL")
    _add_column(conn, "ai_analysis_runs", "actual_cost_usd", "REAL")


def _migration_008_prediction_snapshots(conn: sqlite3.Connection) -> None:
    """Prediction Engine V2: every computed prediction is persisted here
    (not just AI-explained ones — get_prediction() alone triggers a save
    too), so later resolution can be joined back for accuracy/precision/
    recall/Brier/log-loss/calibration/edge/ROI evaluation
    (`evaluation.py::evaluate_predictions`) without needing to have called
    the AI layer at all."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prediction_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            category TEXT,
            prediction_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            market_yes_probability REAL,
            estimated_yes_probability REAL,
            net_yes_edge REAL,
            confidence_score REAL,
            recommendation TEXT NOT NULL,
            comparable_sample_size INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_market
        ON prediction_snapshots(provider, provider_market_id, created_at);
        """
    )


def _migration_009_ai_attempt_tracking(conn: sqlite3.Connection) -> None:
    """Adds full per-attempt transparency after a live smoke test revealed
    that any failed OpenAI call (timeout, invalid JSON, schema mismatch,
    budget block, ...) was persisted identically — no way to tell afterwards
    whether real, billable usage had occurred. Purely additive: existing
    `ai_analysis_runs` rows are untouched and remain readable (new columns
    default to NULL), and `ai_model_attempts` is a brand-new table."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_model_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES ai_analysis_runs(id),
            attempt_number INTEGER NOT NULL,
            is_repair INTEGER NOT NULL DEFAULT 0,
            requested_model TEXT NOT NULL,
            actual_model TEXT,
            status TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            duration_ms INTEGER,
            error_detail TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_model_attempts_run ON ai_model_attempts(run_id);
        """
    )
    _add_column(conn, "ai_analysis_runs", "requested_model", "TEXT")
    _add_column(conn, "ai_analysis_runs", "final_status", "TEXT")
    _add_column(conn, "ai_analysis_runs", "total_attempts", "INTEGER")
    _add_column(conn, "ai_analysis_runs", "repair_attempted", "INTEGER")
    _add_column(conn, "ai_analysis_runs", "total_input_tokens", "INTEGER")
    _add_column(conn, "ai_analysis_runs", "total_output_tokens", "INTEGER")
    _add_column(conn, "ai_analysis_runs", "total_estimated_cost_usd", "REAL")
    _add_column(conn, "ai_analysis_runs", "total_actual_cost_usd", "REAL")


def _migration_010_market_flow_intelligence(conn: sqlite3.Connection) -> None:
    """Adds storage for the public market-flow/order-book/wallet-concentration
    collectors (research-only; no wallet keys, signatures, or transactions —
    only publicly visible on-chain addresses and public CLOB data). Purely
    additive: `orderbook_snapshots` already existed from migration 1 but was
    never written to — reused here rather than duplicated."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS public_trade_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            trade_hash TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            traded_at TEXT NOT NULL,
            side TEXT NOT NULL,
            outcome TEXT,
            price REAL NOT NULL,
            size REAL NOT NULL,
            wallet_address TEXT NOT NULL,
            UNIQUE(provider, provider_market_id, trade_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_public_trade_events_market
        ON public_trade_events(provider, provider_market_id, traded_at);

        CREATE TABLE IF NOT EXISTS public_wallet_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            wallet_address TEXT NOT NULL,
            outcome_index INTEGER,
            amount REAL NOT NULL,
            UNIQUE(provider, provider_market_id, captured_at, wallet_address, outcome_index)
        );
        CREATE INDEX IF NOT EXISTS idx_public_wallet_positions_market
        ON public_wallet_positions(provider, provider_market_id, captured_at);

        CREATE TABLE IF NOT EXISTS wallet_market_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address TEXT NOT NULL,
            markets_seen_in INTEGER NOT NULL DEFAULT 0,
            resolved_markets_seen_in INTEGER NOT NULL DEFAULT 0,
            resolved_correct_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            UNIQUE(wallet_address)
        );

        CREATE TABLE IF NOT EXISTS market_flow_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            status TEXT NOT NULL,
            net_flow REAL,
            large_trade_ratio REAL,
            price_move_without_evidence INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_market_flow_signals_market
        ON market_flow_signals(provider, provider_market_id, captured_at);

        CREATE TABLE IF NOT EXISTS market_reliability_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            captured_at TEXT NOT NULL,
            reliability_level TEXT NOT NULL,
            reliability_score REAL,
            detail TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_market_reliability_market
        ON market_reliability_snapshots(market_id, captured_at);

        CREATE TABLE IF NOT EXISTS manipulation_risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            captured_at TEXT NOT NULL,
            risk_score REAL NOT NULL,
            reasons_json TEXT NOT NULL,
            confidence REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_manipulation_risk_market
        ON manipulation_risk_events(market_id, captured_at);
        """
    )


def _migration_011_shadow_trading(conn: sqlite3.Connection) -> None:
    """Adds the shadow-trading simulation layer: qualified (and blocked)
    shadow decisions, their full lifecycle, and the richer per-snapshot
    fields (independent probability, reliability, manipulation risk,
    opportunity score, submodel breakdown, engine/config version) needed to
    evaluate them later. No real orders, no wallet operations — purely
    additive persistence for a simulation. `shadow_trades` is intentionally
    a new table, separate from the pre-existing `shadow_setups` (an older,
    simpler "research highlight" scorer from migration 6) — the two serve
    different purposes and neither is removed."""
    _add_column(conn, "prediction_snapshots", "independent_probability", "REAL")
    _add_column(conn, "prediction_snapshots", "resolution_clarity", "REAL")
    _add_column(conn, "prediction_snapshots", "market_reliability_score", "REAL")
    _add_column(conn, "prediction_snapshots", "market_reliability_level", "TEXT")
    _add_column(conn, "prediction_snapshots", "manipulation_risk_score", "REAL")
    _add_column(conn, "prediction_snapshots", "opportunity_score", "REAL")
    _add_column(conn, "prediction_snapshots", "deadline_phase", "TEXT")
    _add_column(conn, "prediction_snapshots", "evidence_count", "INTEGER")
    _add_column(conn, "prediction_snapshots", "independent_confirmation_count", "INTEGER")
    _add_column(conn, "prediction_snapshots", "contradiction_present", "INTEGER")
    _add_column(conn, "prediction_snapshots", "orderbook_imbalance", "REAL")
    _add_column(conn, "prediction_snapshots", "net_flow", "REAL")
    _add_column(conn, "prediction_snapshots", "wallet_concentration_score", "REAL")
    _add_column(conn, "prediction_snapshots", "reaction_lag_hours", "REAL")
    _add_column(conn, "prediction_snapshots", "submodel_estimates_json", "TEXT")
    _add_column(conn, "prediction_snapshots", "warnings_json", "TEXT")
    _add_column(conn, "prediction_snapshots", "engine_version", "TEXT")
    _add_column(conn, "prediction_snapshots", "config_hash", "TEXT")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS shadow_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            source_snapshot_id INTEGER REFERENCES prediction_snapshots(id),
            created_at TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_market_price REAL,
            independent_probability REAL,
            expected_edge REAL,
            confidence REAL,
            opportunity_score REAL,
            reliability_score REAL,
            manipulation_risk REAL,
            deadline_phase TEXT,
            assumed_stake REAL NOT NULL DEFAULT 1.0,
            simulated_fee REAL NOT NULL DEFAULT 0.0,
            simulated_slippage REAL NOT NULL DEFAULT 0.0,
            reasons_json TEXT NOT NULL,
            blockers_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'candidate',
            max_favorable_move REAL,
            max_adverse_move REAL,
            max_drawdown REAL,
            price_after_5m REAL,
            price_after_15m REAL,
            price_after_1h REAL,
            price_after_6h REAL,
            price_after_24h REAL,
            price_at_deadline REAL,
            final_resolution_status TEXT,
            final_outcome TEXT,
            simulated_pnl REAL,
            roi REAL,
            holding_hours REAL,
            exit_reason TEXT,
            exit_at TEXT,
            closed_at TEXT,
            engine_version TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_market ON shadow_trades(market_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_status ON shadow_trades(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_shadow_trades_run ON shadow_trades(created_at);
        """
    )


def _migration_012_event_relationship_graph(conn: sqlite3.Connection) -> None:
    """Foundation for Event/Entity/Relation causal-reasoning: canonical
    entities with aliases (entity resolution), events, links from events to
    entities and to Polymarket markets (with a relevance score), and
    directed event/metric relations with an explicit evidence tier. Purely
    additive, deliberately minimal — this is the data model the spec's
    long-term "Event Graph" vision needs, not the graph traversal/
    probability-propagation logic itself, which stays future work."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT NOT NULL UNIQUE,
            entity_type TEXT NOT NULL,
            geographic_scope TEXT
        );

        CREATE TABLE IF NOT EXISTS entity_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL REFERENCES entities(id),
            alias TEXT NOT NULL,
            UNIQUE(alias)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_aliases_alias ON entity_aliases(alias);

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT,
            geographic_scope TEXT,
            source TEXT,
            source_url TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);

        CREATE TABLE IF NOT EXISTS event_entity_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id),
            entity_id INTEGER NOT NULL REFERENCES entities(id),
            role TEXT,
            UNIQUE(event_id, entity_id, role)
        );

        CREATE TABLE IF NOT EXISTS event_market_relevance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id),
            provider TEXT NOT NULL,
            provider_market_id TEXT NOT NULL,
            relevance_score REAL NOT NULL,
            entity_overlap REAL,
            geographic_relevance REAL,
            temporal_relevance REAL,
            detail TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            UNIQUE(event_id, provider, provider_market_id)
        );
        CREATE INDEX IF NOT EXISTS idx_event_market_relevance_market
        ON event_market_relevance(provider, provider_market_id);

        CREATE TABLE IF NOT EXISTS event_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_event_id INTEGER REFERENCES events(id),
            source_entity_id INTEGER REFERENCES entities(id),
            target_entity_id INTEGER REFERENCES entities(id),
            target_provider TEXT,
            target_provider_market_id TEXT,
            relation_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            strength REAL,
            evidence_tier TEXT NOT NULL,
            confidence REAL,
            time_lag_hours REAL,
            geographic_scope TEXT,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            source_quality TEXT,
            valid_from TEXT,
            valid_until TEXT,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_event_relations_target_market
        ON event_relations(target_provider, target_provider_market_id);
        """
    )


def _migration_013_market_classification(conn: sqlite3.Connection) -> None:
    """Phase C: additive columns for the new fixed-taxonomy classifier
    (prediction/classification.py). Deliberately does NOT touch the
    existing `category` column — that column keeps holding whatever the
    provider's raw event/category label was (e.g. Polymarket's
    `events[0].title`), exactly as before, so `history.py`'s
    category-grouping SQL and any existing rows/comparisons stay valid
    unmodified. The new taxonomy category lives in `classified_category`,
    with `classification_confidence` and the event_type detected by
    `semantics.parse_market_proposition` (needed by base_rates.py and
    future comparable-matching) in their own new columns. All nullable,
    all backfilled lazily (only set as markets get (re-)classified) —
    safe on both a fresh DB and an existing populated one."""
    _add_column(conn, "markets", "classified_category", "TEXT")
    _add_column(conn, "markets", "classification_confidence", "REAL")
    _add_column(conn, "markets", "event_type", "TEXT")


def _migration_014_comparable_baseline_history(conn: sqlite3.Connection) -> None:
    """Phase D: additive columns on `markets` for the similarity-weighted
    comparable-case baseline (history.py). Stores the already-computed
    MarketProposition (Phase A) as JSON alongside a lightweight entities
    list, plus a parsed deadline string, so find_comparable_cases() and
    compute_weighted_baseline() have real structured data to score against
    without re-parsing question text on every lookup. Purely additive:
    existing columns/tables are untouched, and every new column is
    nullable so old rows (and rows written by code that hasn't been
    updated yet) remain valid."""
    _add_column(conn, "markets", "proposition_json", "TEXT")
    _add_column(conn, "markets", "entities_json", "TEXT")
    _add_column(conn, "markets", "deadline", "TEXT")


def _migration_015_extracted_event_persistence(conn: sqlite3.Connection) -> None:
    """Phase H: additive columns on the migration-12 `events` table so the
    ExtractedEvent structure that `prediction/semantics.extract_event()`
    already computes during evidence scoring (prediction/evidence.py) can be
    persisted verbatim instead of staying transient. Deliberately reuses
    the existing Event/Entity/Relation event-graph foundation rather than
    inventing a parallel table — `events.event_type`/`source`/`source_url`/
    `occurred_at` already existed; this adds only the fields ExtractedEvent
    has that `events` did not: actors (as JSON, entity resolution is future
    work), action-family, target/matched phrase, status, certainty, and a
    link back to which market's evidence pipeline run produced it. All
    nullable, all backfilled lazily — safe on both a fresh DB and an
    existing populated one. No causal/graph-traversal logic added here,
    just correct storage of already-computed data (provenance = source +
    certainty + created_at, all already present/added)."""
    _add_column(conn, "events", "actors_json", "TEXT")
    _add_column(conn, "events", "action", "TEXT")
    _add_column(conn, "events", "target", "TEXT")
    _add_column(conn, "events", "expected_time", "TEXT")
    _add_column(conn, "events", "status", "TEXT")
    _add_column(conn, "events", "source_type", "TEXT")
    _add_column(conn, "events", "certainty", "TEXT")
    _add_column(conn, "events", "provider", "TEXT")
    _add_column(conn, "events", "provider_market_id", "TEXT")
    _add_column(conn, "events", "news_event_id", "INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_provider_market ON events(provider, provider_market_id)"
    )


def _migration_016_shadow_forecast_calibration_fields(conn: sqlite3.Connection) -> None:
    """Phase N: additive columns on the migration-8 `prediction_snapshots`
    table so every persisted prediction also carries the exact PRE-
    resolution forecast-time fields Phase N2's calibration framework needs
    to eventually join against `market_resolutions` (Phase N2, this same
    round) — Brier score, log-loss, reliability bins, and per-model-family
    error, once enough resolved history exists.

    Deliberately reuses the existing `prediction_snapshots` table (rather
    than a parallel `shadow_forecast_snapshots` table) because it is
    already the "every computed prediction, resolution-independent" record
    (see _migration_008_prediction_snapshots's docstring) with a
    (provider, provider_market_id, created_at) index ready to join against
    `market_resolutions(provider, provider_market_id, resolved_at)`. No
    resolution/outcome column is added here or anywhere in this migration —
    that is the whole point: this table must remain queryable-and-complete
    from forecast-time data alone, with the outcome join happening later,
    read-only, in calibration.py.

    New columns, all nullable, all additive:
      - forecast_at: explicit forecast timestamp (redundant with
        `created_at` today, but named per the Phase N spec and kept
        separate in case snapshot-write time and forecast-computation time
        ever diverge).
      - market_probability_at_forecast: the market price that was passed
        INTO compute_prediction() at call time (identical value to the
        existing `market_yes_probability` column) — named explicitly per
        spec so the calibration join has an unambiguous, self-documenting
        "what the market said before this forecast was made" column,
        decoupled from any future repurposing of `market_yes_probability`.
      - blended_probability / calibrated_probability: the two newer
        PredictionResult numbers (types.py) that `prediction_snapshots` did
        not previously capture (it only had estimated_yes_probability,
        which is blended_probability's V1-era alias).
      - confidence_calibration_status: verbatim copy of
        PredictionResult.confidence_calibration_status (today always the
        literal "UNCALIBRATED" — see types.py's
        DEFAULT_CONFIDENCE_CALIBRATION_STATUS docstring).
      - forecast_status: verbatim copy of PredictionResult.forecast_status
        (e.g. BLENDED_FORECAST, FORECAST_SUPPRESSED, ...).
      - models_used: comma-joined `source` names of every
        contribution_breakdown entry with available=True — which submodels
        actually contributed to this specific forecast.
      - divergence_verdict: PredictionResult.divergence_audit.verdict
        (PASS/WARN/REJECT) when Phase M's audit ran, else NULL (audit
        never triggered / gap below threshold).
      - engine_version: NOTE this column already exists (added in
        migration 8) and is reused as-is for the Phase N spec's
        "engine_version" field (literal tag, e.g. "v1-phaseN") — not
        re-added here.
    """
    _add_column(conn, "prediction_snapshots", "forecast_at", "TEXT")
    _add_column(conn, "prediction_snapshots", "market_probability_at_forecast", "REAL")
    _add_column(conn, "prediction_snapshots", "blended_probability", "REAL")
    _add_column(conn, "prediction_snapshots", "calibrated_probability", "REAL")
    _add_column(conn, "prediction_snapshots", "confidence_calibration_status", "TEXT")
    _add_column(conn, "prediction_snapshots", "forecast_status", "TEXT")
    _add_column(conn, "prediction_snapshots", "models_used", "TEXT")
    _add_column(conn, "prediction_snapshots", "divergence_verdict", "TEXT")


def _migration_017_provider_health_tracking(conn: sqlite3.Connection) -> None:
    """Phase O: provider health tracking infrastructure for live data
    source monitoring. Adds the provider_health table that stores metrics
    for every data source: last success/failure, latency, data age, fetch
    counts, etc. Used by the UI to distinguish LIVE/DEGRADED/STALE/OFFLINE
    provider states and by the scanning pipeline to decide whether to retry
    or skip problematic sources.

    All metrics are additive only — no column is ever dropped or truncated.
    Health records are upserted on every fetch attempt, so the table always
    reflects the most recent provider behavior."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS provider_health (
            source_id TEXT PRIMARY KEY,
            last_success TEXT,
            last_failure TEXT,
            last_failure_reason TEXT,
            last_http_status INTEGER,
            last_latency_ms INTEGER,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            data_age_seconds INTEGER,
            items_fetched INTEGER NOT NULL DEFAULT 0,
            parse_failures INTEGER NOT NULL DEFAULT 0,
            last_check_timestamp TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_provider_health_check
        ON provider_health(last_check_timestamp);
    """)


def _migration_018_claim_extraction_and_verification(conn: sqlite3.Connection) -> None:
    """Phase O: Claim extraction, deduplication, and verification foundation.
    
    Adds tables for structured claim tracking:
      - claims: individual claims extracted from articles
      - claim_groups: deduplicated groups of equivalent claims
      - claim_sources: mapping between claims and their sources
    
    These tables support:
      - Multi-claim articles (one article may contain multiple claims)
      - Claim deduplication (Reuters + Yahoo copy = 1 claim, not 2)
      - Verification states (UNVERIFIED, SINGLE_SOURCE, MULTI_SOURCE, etc.)
      - Counterevidence tracking (contradicting claims)
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS claims (
            claim_id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT,
            speaker TEXT,
            source_id TEXT,
            source_url TEXT,
            timestamp TEXT,
            verification_status TEXT NOT NULL,
            confidence REAL NOT NULL,
            entities_json TEXT,
            location TEXT,
            raw_reference TEXT,
            event_type TEXT,
            direction TEXT,
            created_at TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS claim_groups (
            claim_id TEXT PRIMARY KEY,
            canonical_claim_id TEXT NOT NULL,
            republishing_sources_json TEXT,
            independent_sources INTEGER DEFAULT 1,
            confirmation_count INTEGER DEFAULT 1,
            verification_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (canonical_claim_id) REFERENCES claims(claim_id)
        );
        
        CREATE TABLE IF NOT EXISTS claim_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            source_url TEXT,
            timestamp TEXT,
            FOREIGN KEY (claim_id) REFERENCES claims(claim_id),
            UNIQUE(claim_id, source_id)
        );
        
        CREATE TABLE IF NOT EXISTS claim_counter_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            contradicts_claim_id TEXT NOT NULL,
            source_id TEXT,
            source_url TEXT,
            timestamp TEXT,
            confidence REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (claim_id) REFERENCES claims(claim_id),
            FOREIGN KEY (contradicts_claim_id) REFERENCES claims(claim_id)
        );
        
        CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject);
        CREATE INDEX IF NOT EXISTS idx_claims_source_id ON claims(source_id);
        CREATE INDEX IF NOT EXISTS idx_claims_verification_status ON claims(verification_status);
        CREATE INDEX IF NOT EXISTS idx_claim_groups_canonical ON claim_groups(canonical_claim_id);
        CREATE INDEX IF NOT EXISTS idx_claim_sources_claim ON claim_sources(claim_id);
        CREATE INDEX IF NOT EXISTS idx_claim_counter_evidence_claim ON claim_counter_evidence(claim_id);
    """)


def _migration_019_macro_observations(conn: sqlite3.Connection) -> None:
    """Additive: persists real FRED macro observations (FEDFUNDS/CPIAUCSL/
    UNRATE) fetched by providers/fred.py, with a fetch timestamp so
    freshness can be scored later. Mirrors the provider_health pattern
    (upsert-by-key, never drops/truncates). Not yet consulted as a read-
    through cache by the live fetch path (same precedent as CoinGecko,
    which also fetches fresh on every call rather than caching) — this
    table exists so a fetched snapshot survives the process and its
    freshness is auditable, which is the additive goal for this round."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS macro_observations (
            series_id TEXT NOT NULL,
            observation_date TEXT NOT NULL,
            value REAL NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (series_id, observation_date)
        );

        CREATE INDEX IF NOT EXISTS idx_macro_observations_fetched_at
        ON macro_observations(fetched_at);
    """)


def _migration_020_polymarket_price_history_backfill(conn: sqlite3.Connection) -> None:
    """Additive: real historical Polymarket CLOB price points backfilled for
    already-resolved markets (see scripts/backfill_polymarket_price_history.py).
    Deliberately a NEW table rather than reusing market_snapshots — that
    table's schema (NOT NULL run_id FK to scanner_runs, NOT NULL liquidity/
    volume_24h/volume_total/opportunity_score/reasons) models a *live scan
    snapshot* and does not fit a bare (timestamp, price) point pulled from
    the CLOB /prices-history endpoint. captured_at is the REAL historical
    timestamp the price point represents (from the API's "t" field), never
    the fetch time. Purely additive: no existing table/column touched."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS polymarket_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL REFERENCES markets(market_id),
            condition_id TEXT,
            token_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            yes_price REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'polymarket_backfill',
            fetched_at TEXT NOT NULL,
            UNIQUE(market_id, token_id, captured_at)
        );

        CREATE INDEX IF NOT EXISTS idx_polymarket_price_history_market_captured
        ON polymarket_price_history(market_id, captured_at);
    """)


def _migration_021_claim_resolution_step(conn: sqlite3.Connection) -> None:
    """BLOCK C, Part 1/3: additive column on the existing `claims` table
    (migration 018) linking a claim to a real ResolutionStep name (see
    prediction/world_state.py's ResolutionStep/ResolutionPath, added in the
    same block) for markets with a known multi-step resolution structure
    (currently only legislation). NULL for every claim that doesn't map to
    a recognized step — the honest, common case — never backfilled/guessed.
    Purely additive: no existing column dropped or altered."""
    _add_column(conn, "claims", "resolution_step", "TEXT")
    conn.commit()


def _migration_022_prediction_snapshot_forecast_semantics(conn: sqlite3.Connection) -> None:
    """BLOCK E, Part 4: additive columns on the existing `prediction_snapshots`
    table (migration 8, extended by migrations 14/16) so a market's forecast
    history genuinely carries Block A's four-tier forecast-semantics
    separation (market_probability/model_hypothesis_probability/
    evidence_backed_probability/published_forecast_probability), plus
    forecast_maturity and a real evidence-strength/data-quality signal at
    the time each snapshot was taken. Every column is nullable and purely
    additive — no existing column dropped, renamed, or altered; every
    existing row keeps reading exactly as it did before this migration
    (all new columns NULL for pre-Block-A snapshots, which is honest: those
    rows genuinely predate the four-tier separation existing at all)."""
    _add_column(conn, "prediction_snapshots", "model_hypothesis_probability", "REAL")
    _add_column(conn, "prediction_snapshots", "evidence_backed_probability", "REAL")
    _add_column(conn, "prediction_snapshots", "published_forecast_probability", "REAL")
    _add_column(conn, "prediction_snapshots", "forecast_maturity", "TEXT")
    _add_column(conn, "prediction_snapshots", "evidence_strength", "TEXT")
    _add_column(conn, "prediction_snapshots", "data_quality_composite_score", "REAL")
    conn.commit()


MIGRATIONS: list[Migration] = [
    (1, "initial_schema", _migration_001_initial),
    (2, "provider_architecture", _migration_002_provider_architecture),
    (3, "watchlist", _migration_003_watchlist),
    (4, "intelligence_platform", _migration_004_intelligence_platform),
    (5, "ai_analysis", _migration_005_ai_analysis),
    (6, "shadow_setups", _migration_006_shadow_setups),
    (7, "ai_cost_tracking", _migration_007_ai_cost_tracking),
    (8, "prediction_snapshots", _migration_008_prediction_snapshots),
    (9, "ai_attempt_tracking", _migration_009_ai_attempt_tracking),
    (10, "market_flow_intelligence", _migration_010_market_flow_intelligence),
    (11, "shadow_trading", _migration_011_shadow_trading),
    (12, "event_relationship_graph", _migration_012_event_relationship_graph),
    (13, "market_classification", _migration_013_market_classification),
    (14, "comparable_baseline_history", _migration_014_comparable_baseline_history),
    (15, "extracted_event_persistence", _migration_015_extracted_event_persistence),
    (16, "shadow_forecast_calibration_fields", _migration_016_shadow_forecast_calibration_fields),
    (17, "provider_health_tracking", _migration_017_provider_health_tracking),
    (18, "claim_extraction_and_verification", _migration_018_claim_extraction_and_verification),
    (19, "macro_observations", _migration_019_macro_observations),
    (20, "polymarket_price_history_backfill", _migration_020_polymarket_price_history_backfill),
    (21, "claim_resolution_step", _migration_021_claim_resolution_step),
    (22, "prediction_snapshot_forecast_semantics", _migration_022_prediction_snapshot_forecast_semantics),
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
