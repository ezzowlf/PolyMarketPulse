from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .migrations import current_schema_version, run_migrations
from .models import Market, ResolutionStatus, Signal
from .providers.base import ProviderCapabilities
from .signals import PreviousSnapshot

STATUS_TABLES = (
    "markets",
    "market_snapshots",
    "price_history",
    "scanner_runs",
    "signals",
    "signal_outcomes",
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
)


def _row_key(market: Market) -> str:
    """Cross-provider-unique storage key for the `markets` table."""
    return f"{market.provider}:{market.provider_market_id}"


def _snapshot_fingerprint(market: Market, score: float) -> str:
    return json.dumps(
        (
            market.yes_price,
            market.no_price,
            market.best_bid,
            market.best_ask,
            round(market.liquidity, 2),
            round(market.volume_24h, 2),
            market.spread,
            score,
        )
    )


class Storage:
    def __init__(
        self, path: Path, store_unchanged_snapshots: bool = False, auto_migrate: bool = True
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # timeout + WAL let multiple short-lived connections (one per API
        # request) coexist without "database is locked" errors under
        # concurrent reads/writes from the dashboard. check_same_thread=False
        # because FastAPI's threadpool may open and close a sync dependency's
        # connection on different worker threads for the same request.
        self.connection = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.store_unchanged_snapshots = store_unchanged_snapshots
        if auto_migrate:
            self.migrate()

    def migrate(self) -> list[int]:
        return run_migrations(self.connection)

    def schema_version(self) -> int:
        return current_schema_version(self.connection)

    # --- scanner runs -----------------------------------------------------

    def start_run(self, provider: str) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            "INSERT INTO scanner_runs (started_at, provider, status) VALUES (?, ?, ?)",
            (now, provider, "started"),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        markets_read: int,
        markets_saved: int,
        markets_failed: int = 0,
        duration_ms: int | None = None,
        error_details: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            UPDATE scanner_runs SET
                finished_at = ?, status = ?, markets_fetched = ?, signals_saved = ?,
                markets_read = ?, markets_saved = ?, markets_failed = ?,
                duration_ms = ?, error_details = ?
            WHERE id = ?
            """,
            (
                now,
                status,
                markets_read,
                markets_saved,
                markets_read,
                markets_saved,
                markets_failed,
                duration_ms,
                error_details,
                run_id,
            ),
        )
        self.connection.commit()

    # --- providers ----------------------------------------------------------

    def register_provider(self, name: str, display_name: str, capabilities: ProviderCapabilities) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            INSERT INTO providers (name, display_name, real_money, requires_auth, capabilities_json,
                                    first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                display_name = excluded.display_name,
                real_money = excluded.real_money,
                requires_auth = excluded.requires_auth,
                capabilities_json = excluded.capabilities_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                name,
                display_name,
                int(capabilities.real_money),
                int(capabilities.requires_auth),
                json.dumps(capabilities.as_dict()),
                now,
                now,
            ),
        )
        self.connection.commit()

    # --- last snapshot lookup (for change-detection / signal deltas) ------

    def get_previous_snapshot(self, provider: str, provider_market_id: str) -> PreviousSnapshot | None:
        row = self.connection.execute(
            """
            SELECT ms.liquidity, ms.volume_24h, ms.spread, ms.yes_price, ms.one_day_change
            FROM market_snapshots ms
            JOIN markets m ON m.market_id = ms.market_id
            WHERE m.provider = ? AND m.provider_market_id = ?
            ORDER BY ms.captured_at DESC LIMIT 1
            """,
            (provider, provider_market_id),
        ).fetchone()
        if row is None:
            return None
        return PreviousSnapshot(*row)

    # --- save a scan batch --------------------------------------------------

    def save(self, run_id: int, market_signals: list[tuple[Market, list[Signal]]]) -> int:
        """Persist markets, snapshots, price history and research signals for
        one scan batch. Returns the number of snapshots actually written
        (skipping unchanged snapshots unless `store_unchanged_snapshots`)."""
        now = datetime.now(UTC).isoformat()
        snapshots_written = 0

        for market, signals in market_signals:
            key = _row_key(market)
            self.connection.execute(
                """
                INSERT INTO markets (
                    market_id, provider, provider_market_id, condition_id, question, slug,
                    category, tags, url, yes_token_id, no_token_id, start_date, end_date,
                    event_id, description, outcomes, outcome_prices, resolved_at,
                    resolution_status, winning_outcome, resolution_source, raw_data_hash,
                    provider_data, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_market_id) DO UPDATE SET
                    condition_id = excluded.condition_id,
                    question = excluded.question,
                    slug = excluded.slug,
                    category = excluded.category,
                    tags = excluded.tags,
                    url = excluded.url,
                    yes_token_id = excluded.yes_token_id,
                    no_token_id = excluded.no_token_id,
                    start_date = excluded.start_date,
                    end_date = excluded.end_date,
                    event_id = excluded.event_id,
                    description = excluded.description,
                    outcomes = excluded.outcomes,
                    outcome_prices = excluded.outcome_prices,
                    resolved_at = excluded.resolved_at,
                    resolution_status = excluded.resolution_status,
                    winning_outcome = excluded.winning_outcome,
                    resolution_source = excluded.resolution_source,
                    raw_data_hash = excluded.raw_data_hash,
                    provider_data = excluded.provider_data,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    key,
                    market.provider,
                    market.provider_market_id,
                    market.condition_id,
                    market.question,
                    market.slug,
                    market.category,
                    ", ".join(market.tags),
                    market.url,
                    market.yes_token_id,
                    market.no_token_id,
                    market.start_at.isoformat() if market.start_at else None,
                    market.end_at.isoformat() if market.end_at else None,
                    market.event_id,
                    market.description,
                    json.dumps(list(market.outcomes)),
                    json.dumps(list(market.outcome_prices)),
                    market.resolved_at.isoformat() if market.resolved_at else None,
                    market.resolution_status.value,
                    market.winning_outcome,
                    market.resolution_source,
                    market.raw_data_hash,
                    json.dumps(market.provider_data),
                    now,
                    now,
                ),
            )

            # The stored market_id may differ from `key` for rows that
            # pre-date the provider-prefixed key format (migrated Phase-1
            # data); always use the canonical value actually on the row so
            # child-table foreign keys stay consistent and no duplicate
            # `markets` row is created.
            key = self.connection.execute(
                "SELECT market_id FROM markets WHERE provider = ? AND provider_market_id = ?",
                (market.provider, market.provider_market_id),
            ).fetchone()[0]

            base_score = signals[0].score if signals else 0.0
            fingerprint = _snapshot_fingerprint(market, base_score)
            previous_fp = self.connection.execute(
                "SELECT reasons FROM market_snapshots WHERE market_id = ? "
                "ORDER BY captured_at DESC LIMIT 1",
                (key,),
            ).fetchone()
            unchanged = previous_fp is not None and previous_fp[0] == fingerprint
            should_write = self.store_unchanged_snapshots or not unchanged

            if should_write:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO market_snapshots (
                        run_id, captured_at, market_id, provider, yes_price, no_price,
                        best_bid, best_ask, liquidity, volume_24h, volume_total, spread,
                        one_day_change, opportunity_score, reasons, is_heartbeat
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        now,
                        key,
                        market.provider,
                        market.yes_price,
                        market.no_price,
                        market.best_bid,
                        market.best_ask,
                        market.liquidity,
                        market.volume_24h,
                        market.volume_total,
                        market.spread,
                        market.one_day_change,
                        base_score,
                        fingerprint,
                        int(unchanged),
                    ),
                )
                snapshots_written += 1

                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO price_history (market_id, provider, captured_at, yes_price, no_price)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, market.provider, now, market.yes_price, market.no_price),
                )

            days_to_resolution = None
            if market.end_at is not None:
                days_to_resolution = (market.end_at - datetime.now(UTC)).total_seconds() / 86400

            for signal in signals:
                self.connection.execute(
                    """
                    INSERT INTO research_signals (
                        run_id, provider, provider_market_id, captured_at, signal_type, score,
                        reasons, subfactors_json, origin_yes_price, origin_liquidity,
                        origin_days_to_resolution, forecast_probability, data_quality_flag, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        market.provider,
                        market.provider_market_id,
                        now,
                        signal.signal_type,
                        signal.score,
                        ", ".join(signal.reasons),
                        json.dumps(signal.subfactors),
                        market.yes_price,
                        market.liquidity,
                        days_to_resolution,
                        signal.forecast_probability,
                        int(bool(market.missing_fields)),
                        "open",
                    ),
                )

            # Legacy signals table kept for backward compatibility with
            # Phase-1 data; not extended further.
            if signals:
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO signals (run_id, market_id, captured_at, opportunity_score, reasons)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, key, now, base_score, ", ".join(signals[0].reasons)),
                )

        self.connection.commit()
        return snapshots_written

    # --- resolutions ----------------------------------------------------------

    TERMINAL_RESOLUTION_STATUSES = (
        ResolutionStatus.RESOLVED,
        ResolutionStatus.CANCELLED,
        ResolutionStatus.INVALID,
        ResolutionStatus.DISPUTED,
    )

    def record_resolution(self, market: Market) -> bool:
        """Idempotently record a market that has reached a terminal
        resolution state (resolved, cancelled, invalid, or disputed).
        Returns True if this call newly inserted or changed the row."""
        if market.resolution_status not in self.TERMINAL_RESOLUTION_STATUSES:
            return False
        now = datetime.now(UTC).isoformat()
        existing = self.connection.execute(
            "SELECT winning_outcome, status FROM market_resolutions WHERE provider = ? AND provider_market_id = ?",
            (market.provider, market.provider_market_id),
        ).fetchone()
        status_value = market.resolution_status.value
        if existing is not None and existing[0] == market.winning_outcome and existing[1] == status_value:
            return False

        self.connection.execute(
            """
            INSERT INTO market_resolutions (
                provider, provider_market_id, resolved_at, winning_outcome,
                final_yes_price, final_no_price, resolution_source, status, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_market_id) DO UPDATE SET
                resolved_at = excluded.resolved_at,
                winning_outcome = excluded.winning_outcome,
                final_yes_price = excluded.final_yes_price,
                final_no_price = excluded.final_no_price,
                resolution_source = excluded.resolution_source,
                status = excluded.status,
                detected_at = excluded.detected_at
            """,
            (
                market.provider,
                market.provider_market_id,
                market.resolved_at.isoformat() if market.resolved_at else now,
                market.winning_outcome,
                market.yes_price,
                market.no_price,
                market.resolution_source,
                status_value,
                now,
            ),
        )
        self.connection.commit()
        self._evaluate_open_signals(market)
        return True

    def _evaluate_open_signals(self, market: Market) -> None:
        """Give any still-open research signals for this market a result now
        that it has resolved. Simulated only: a 1-unit virtual stake, never
        real money."""
        resolution_row = self.connection.execute(
            "SELECT id, resolved_at FROM market_resolutions WHERE provider = ? AND provider_market_id = ?",
            (market.provider, market.provider_market_id),
        ).fetchone()
        if resolution_row is None:
            return
        resolution_id, resolved_at = resolution_row

        open_signals = self.connection.execute(
            """
            SELECT id, captured_at, origin_yes_price FROM research_signals
            WHERE provider = ? AND provider_market_id = ? AND status = 'open'
            """,
            (market.provider, market.provider_market_id),
        ).fetchall()

        is_decisive = market.resolution_status == ResolutionStatus.RESOLVED
        won = is_decisive and market.winning_outcome is not None and market.winning_outcome.lower() == "yes"
        now = datetime.now(UTC).isoformat()

        for signal_id, captured_at, origin_yes_price in open_signals:
            correct = None
            pnl = None
            if not is_decisive:
                # Cancelled/invalid/disputed markets have no winner — treat
                # the simulated 1-unit stake as refunded (0 P&L), and never
                # score correctness for an outcome that never happened.
                pnl = 0.0
            elif origin_yes_price is not None:
                # Simulated 1-virtual-unit stake on YES at the signal's origin price.
                correct = (origin_yes_price >= 0.5) == won
                pnl = (1.0 - origin_yes_price) if won else (-origin_yes_price)

            hold_hours = None
            try:
                started = datetime.fromisoformat(captured_at)
                ended = datetime.fromisoformat(resolved_at)
                hold_hours = (ended - started).total_seconds() / 3600
            except ValueError:
                pass

            price_extremes = self.connection.execute(
                """
                SELECT MIN(yes_price), MAX(yes_price) FROM price_history
                WHERE provider = ? AND market_id = ? AND captured_at >= ?
                """,
                (market.provider, _row_key(market), captured_at),
            ).fetchone()
            min_price, max_price = price_extremes if price_extremes else (None, None)
            mfe = mae = None
            if origin_yes_price is not None and won and max_price is not None:
                mfe = max_price - origin_yes_price
            if origin_yes_price is not None and min_price is not None:
                mae = origin_yes_price - min_price

            self.connection.execute(
                """
                INSERT INTO signal_evaluations (
                    signal_id, resolution_id, origin_yes_price, final_outcome, correct,
                    simulated_pnl_per_unit, hold_duration_hours, max_favorable_excursion,
                    max_adverse_excursion, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    final_outcome = excluded.final_outcome,
                    correct = excluded.correct,
                    simulated_pnl_per_unit = excluded.simulated_pnl_per_unit,
                    hold_duration_hours = excluded.hold_duration_hours,
                    evaluated_at = excluded.evaluated_at
                """,
                (
                    signal_id,
                    resolution_id,
                    origin_yes_price,
                    market.winning_outcome,
                    None if correct is None else int(correct),
                    pnl,
                    hold_hours,
                    mfe,
                    mae,
                    now,
                ),
            )
            self.connection.execute(
                "UPDATE research_signals SET status = 'resolved' WHERE id = ?", (signal_id,)
            )
        self.connection.commit()

    # --- alerts (Telegram cooldown) ----------------------------------------

    def markets_alerted_since(self, market_keys: list[str], since_iso: str) -> set[str]:
        if not market_keys:
            return set()
        placeholders = ",".join("?" for _ in market_keys)
        rows = self.connection.execute(
            f"SELECT market_id FROM markets WHERE market_id IN ({placeholders}) "
            "AND last_alerted_at IS NOT NULL AND last_alerted_at >= ?",
            (*market_keys, since_iso),
        ).fetchall()
        return {row[0] for row in rows}

    def record_alerts(self, market_keys: list[str]) -> None:
        if not market_keys:
            return
        now = datetime.now(UTC).isoformat()
        self.connection.executemany(
            "UPDATE markets SET last_alerted_at = ? WHERE market_id = ?",
            [(now, key) for key in market_keys],
        )
        self.connection.commit()

    # --- status / stats -------------------------------------------------------

    def status(self) -> dict:
        cur = self.connection.cursor()
        counts = {}
        for table in STATUS_TABLES:
            try:
                counts[table] = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                counts[table] = None
        last_run = cur.execute(
            "SELECT started_at, finished_at, markets_fetched, provider, status "
            "FROM scanner_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            **counts,
            "schema_version": self.schema_version(),
            "last_run_started_at": last_run[0] if last_run else None,
            "last_run_finished_at": last_run[1] if last_run else None,
            "last_run_markets_fetched": last_run[2] if last_run else None,
            "last_run_provider": last_run[3] if last_run else None,
            "last_run_status": last_run[4] if last_run else None,
        }

    # --- news -----------------------------------------------------------------

    def save_news_event(self, event) -> int | None:
        """Insert a news event, skipping if its content_hash already exists
        (deduplication). Returns the row id, or None if it was a duplicate."""
        existing = self.connection.execute(
            "SELECT id FROM news_events WHERE content_hash = ?", (event.content_hash,)
        ).fetchone()
        if existing is not None:
            return None
        cursor = self.connection.execute(
            """
            INSERT INTO news_events (source, source_url, title, published_at, fetched_at,
                                      content_hash, entities_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_url) DO NOTHING
            """,
            (
                event.source,
                event.source_url,
                event.title,
                event.published_at.isoformat() if event.published_at else None,
                event.fetched_at.isoformat(),
                event.content_hash,
                json.dumps(list(getattr(event, "entities", ()))),
            ),
        )
        self.connection.commit()
        return cursor.lastrowid or None

    def save_news_market_link(self, news_event_id: int, link) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            INSERT INTO news_market_links (news_event_id, provider, provider_market_id,
                                            match_reason, matched_terms, confidence, confirmed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(news_event_id, provider, provider_market_id) DO NOTHING
            """,
            (
                news_event_id,
                link.market.provider,
                link.market.provider_market_id,
                link.match_reason,
                ", ".join(link.matched_terms),
                link.confidence,
                link.confirmed,
                now,
            ),
        )
        self.connection.commit()

    # --- cross-provider matching -----------------------------------------------

    def save_market_match(self, candidate) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            INSERT INTO market_matches (
                provider_a, provider_market_id_a, provider_b, provider_market_id_b,
                text_similarity, date_similarity, outcome_structure_match, category_match,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_a, provider_market_id_a, provider_b, provider_market_id_b)
            DO UPDATE SET text_similarity = excluded.text_similarity,
                          date_similarity = excluded.date_similarity
            """,
            (
                candidate.market_a.provider,
                candidate.market_a.provider_market_id,
                candidate.market_b.provider,
                candidate.market_b.provider_market_id,
                candidate.text_similarity,
                candidate.date_similarity,
                None if candidate.outcome_structure_match is None else int(candidate.outcome_structure_match),
                None if candidate.category_match is None else int(candidate.category_match),
                candidate.status,
                now,
            ),
        )
        self.connection.commit()

    # --- watchlist --------------------------------------------------------

    def list_watchlist(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT w.id, w.provider, w.provider_market_id, w.note, w.alert_rules_json,
                   w.created_at, m.question, m.url, w.tags, w.rating, w.group_name,
                   w.virtual_position_json
            FROM watchlist_items w
            LEFT JOIN markets m ON m.provider = w.provider AND m.provider_market_id = w.provider_market_id
            ORDER BY w.created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": r[0],
                "provider": r[1],
                "provider_market_id": r[2],
                "note": r[3],
                "alert_rules": json.loads(r[4]) if r[4] else {},
                "created_at": r[5],
                "question": r[6],
                "url": r[7],
                "tags": json.loads(r[8]) if r[8] else [],
                "rating": r[9],
                "group": r[10],
                "virtual_position": json.loads(r[11]) if r[11] else None,
            }
            for r in rows
        ]

    def add_watchlist_item(
        self,
        provider: str,
        provider_market_id: str,
        note: str | None,
        alert_rules: dict | None,
        tags: list[str] | None = None,
        rating: int | None = None,
        group: str | None = None,
        virtual_position: dict | None = None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            INSERT INTO watchlist_items (
                provider, provider_market_id, note, alert_rules_json, created_at,
                tags, rating, group_name, virtual_position_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_market_id) DO UPDATE SET
                note = excluded.note, alert_rules_json = excluded.alert_rules_json,
                tags = excluded.tags, rating = excluded.rating, group_name = excluded.group_name,
                virtual_position_json = excluded.virtual_position_json
            """,
            (
                provider,
                provider_market_id,
                note,
                json.dumps(alert_rules or {}),
                now,
                json.dumps(tags or []),
                rating,
                group,
                json.dumps(virtual_position) if virtual_position else None,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT id FROM watchlist_items WHERE provider = ? AND provider_market_id = ?",
            (provider, provider_market_id),
        ).fetchone()
        return int(row[0])

    def remove_watchlist_item(self, item_id: int) -> bool:
        cursor = self.connection.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    # --- data quality -----------------------------------------------------

    def save_quality_reports(self, run_id: int | None, reports: list) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.executemany(
            """
            INSERT INTO data_quality_reports (
                run_id, provider, provider_market_id, captured_at, score, issues_json, checks_passed_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    provider,
                    report.market_id,
                    now,
                    report.score,
                    json.dumps(list(report.issues)),
                    json.dumps(list(report.checks_passed)),
                )
                for provider, report in reports
            ],
        )
        self.connection.commit()

    def latest_quality_reports(self, provider: str | None = None) -> list[dict]:
        conditions = []
        params: list[str] = []
        if provider:
            conditions.append("provider = ?")
            params.append(provider)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.connection.execute(
            f"""
            SELECT dq.provider, dq.provider_market_id, dq.captured_at, dq.score, dq.issues_json,
                   dq.checks_passed_json, m.question
            FROM data_quality_reports dq
            JOIN (
                SELECT provider, provider_market_id, MAX(captured_at) AS latest
                FROM data_quality_reports GROUP BY provider, provider_market_id
            ) latest ON latest.provider = dq.provider AND latest.provider_market_id = dq.provider_market_id
                     AND latest.latest = dq.captured_at
            LEFT JOIN markets m ON m.provider = dq.provider AND m.provider_market_id = dq.provider_market_id
            {where}
            ORDER BY dq.score ASC
            """,
            params,
        ).fetchall()
        return [
            {
                "provider": r[0],
                "provider_market_id": r[1],
                "captured_at": r[2],
                "score": r[3],
                "issues": json.loads(r[4]),
                "checks_passed": json.loads(r[5]),
                "question": r[6],
            }
            for r in rows
        ]

    # --- global search ------------------------------------------------------

    def search(self, term: str, limit: int = 20) -> dict:
        like = f"%{term}%"
        markets = self.connection.execute(
            "SELECT market_id, question, provider, category FROM markets "
            "WHERE question LIKE ? OR category LIKE ? OR tags LIKE ? LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        news = self.connection.execute(
            "SELECT id, title, source, published_at FROM news_events WHERE title LIKE ? LIMIT ?",
            (like, limit),
        ).fetchall()
        signals = self.connection.execute(
            "SELECT id, signal_type, provider, provider_market_id FROM research_signals "
            "WHERE signal_type LIKE ? LIMIT ?",
            (like, limit),
        ).fetchall()
        resolutions = self.connection.execute(
            "SELECT provider, provider_market_id, status, winning_outcome FROM market_resolutions "
            "WHERE winning_outcome LIKE ? OR status LIKE ? LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return {
            "markets": [dict(zip(("market_id", "question", "provider", "category"), r, strict=True)) for r in markets],
            "news": [dict(zip(("id", "title", "source", "published_at"), r, strict=True)) for r in news],
            "signals": [dict(zip(("id", "signal_type", "provider", "provider_market_id"), r, strict=True)) for r in signals],
            "resolutions": [dict(zip(("provider", "provider_market_id", "status", "winning_outcome"), r, strict=True)) for r in resolutions],
        }

    # --- AI analysis runs (also the cache store) ---------------------------

    def find_cached_ai_run(
        self, analysis_type: str, model: str, prompt_version: str, context_hash: str, ttl_seconds: int
    ) -> dict | None:
        cutoff = (datetime.now(UTC) - timedelta(seconds=ttl_seconds)).isoformat()
        row = self.connection.execute(
            """
            SELECT id, response_json, input_tokens, output_tokens, created_at
            FROM ai_analysis_runs
            WHERE analysis_type = ? AND model = ? AND prompt_version = ? AND context_hash = ?
              AND status = 'completed' AND created_at >= ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (analysis_type, model, prompt_version, context_hash, cutoff),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "response_json": row[1],
            "input_tokens": row[2],
            "output_tokens": row[3],
            "created_at": row[4],
        }

    def record_ai_run(
        self,
        analysis_type: str,
        market_id: str | None,
        model: str,
        prompt_version: str,
        context_hash: str,
        status: str,
        duration_ms: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cached: bool,
        error_code: str | None,
        response_json: str | None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO ai_analysis_runs (
                analysis_type, market_id, model, prompt_version, context_hash, status,
                created_at, duration_ms, input_tokens, output_tokens, cached, error_code, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_type,
                market_id,
                model,
                prompt_version,
                context_hash,
                status,
                now,
                duration_ms,
                input_tokens,
                output_tokens,
                int(cached),
                error_code,
                response_json,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list_ai_runs(self, limit: int = 20) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT id, analysis_type, market_id, model, status, created_at, duration_ms,
                   input_tokens, output_tokens, cached, error_code
            FROM ai_analysis_runs ORDER BY created_at DESC LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
        cols = (
            "id", "analysis_type", "market_id", "model", "status", "created_at", "duration_ms",
            "input_tokens", "output_tokens", "cached", "error_code",
        )
        return [dict(zip(cols, r, strict=True)) for r in rows]

    # --- Shadow-Setups (permanente Research-Historie) ---------------------

    def save_shadow_setup(self, run_id: int | None, setup) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO shadow_setups (
                run_id, provider, provider_market_id, created_at, score, breakdown_json,
                warum_interessant_json, warum_nicht_json, was_fehlt_json, confirming_factor_count,
                origin_yes_price, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'aktiv')
            """,
            (
                run_id,
                setup.market.provider,
                setup.market.provider_market_id,
                now,
                setup.score,
                json.dumps(setup.breakdown.as_dict()),
                json.dumps(list(setup.warum_interessant)),
                json.dumps(list(setup.warum_nicht)),
                json.dumps(list(setup.was_fehlt)),
                setup.confirming_factor_count,
                setup.market.yes_price,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def has_recent_shadow_setup(self, provider: str, provider_market_id: str, since_iso: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM shadow_setups WHERE provider = ? AND provider_market_id = ? "
            "AND created_at >= ? LIMIT 1",
            (provider, provider_market_id, since_iso),
        ).fetchone()
        return row is not None

    def list_shadow_setups(self, status: str | None = None, limit: int = 20) -> list[dict]:
        conditions = []
        params: list = []
        if status:
            conditions.append("s.status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(max(1, min(limit, 200)))
        rows = self.connection.execute(
            f"""
            SELECT s.id, s.provider, s.provider_market_id, s.created_at, s.score, s.breakdown_json,
                   s.warum_interessant_json, s.warum_nicht_json, s.was_fehlt_json,
                   s.confirming_factor_count, s.status, s.origin_yes_price, s.resolved_at,
                   s.final_outcome, s.final_yes_price, s.duration_hours, m.question, m.url,
                   m.market_id, m.end_date
            FROM shadow_setups s
            LEFT JOIN markets m ON m.provider = s.provider AND m.provider_market_id = s.provider_market_id
            {where}
            ORDER BY s.created_at DESC LIMIT ?
            """,
            params,
        ).fetchall()
        cols = (
            "id", "provider", "provider_market_id", "created_at", "score", "breakdown",
            "warum_interessant", "warum_nicht", "was_fehlt", "confirming_factor_count", "status",
            "origin_yes_price", "resolved_at", "final_outcome", "final_yes_price", "duration_hours",
            "question", "url", "market_id", "end_date",
        )
        results = []
        for r in rows:
            item = dict(zip(cols, r, strict=True))
            item["breakdown"] = json.loads(item["breakdown"])
            item["warum_interessant"] = json.loads(item["warum_interessant"])
            item["warum_nicht"] = json.loads(item["warum_nicht"])
            item["was_fehlt"] = json.loads(item["was_fehlt"])
            results.append(item)
        return results

    def get_shadow_setup(self, setup_id: int) -> dict | None:
        matches = [s for s in self.list_shadow_setups(limit=200) if s["id"] == setup_id]
        if matches:
            return matches[0]
        row = self.connection.execute(
            """
            SELECT s.id, s.provider, s.provider_market_id, s.created_at, s.score, s.breakdown_json,
                   s.warum_interessant_json, s.warum_nicht_json, s.was_fehlt_json,
                   s.confirming_factor_count, s.status, s.origin_yes_price, s.resolved_at,
                   s.final_outcome, s.final_yes_price, s.duration_hours, m.question, m.url,
                   m.market_id, m.end_date
            FROM shadow_setups s
            LEFT JOIN markets m ON m.provider = s.provider AND m.provider_market_id = s.provider_market_id
            WHERE s.id = ?
            """,
            (setup_id,),
        ).fetchone()
        if row is None:
            return None
        cols = (
            "id", "provider", "provider_market_id", "created_at", "score", "breakdown",
            "warum_interessant", "warum_nicht", "was_fehlt", "confirming_factor_count", "status",
            "origin_yes_price", "resolved_at", "final_outcome", "final_yes_price", "duration_hours",
            "question", "url", "market_id", "end_date",
        )
        item = dict(zip(cols, row, strict=True))
        item["breakdown"] = json.loads(item["breakdown"])
        item["warum_interessant"] = json.loads(item["warum_interessant"])
        item["warum_nicht"] = json.loads(item["warum_nicht"])
        item["was_fehlt"] = json.loads(item["was_fehlt"])
        return item

    def resolve_shadow_setups_for_market(self, market: Market) -> int:
        """Closes out any still-active Shadow-Setups for a market that just
        resolved, recording how it actually ended. Idempotent: markets with
        no active setups are a no-op."""
        if market.resolution_status.value not in ("resolved", "cancelled", "invalid", "disputed"):
            return 0
        now = datetime.now(UTC).isoformat()
        rows = self.connection.execute(
            "SELECT id, created_at FROM shadow_setups WHERE provider = ? AND provider_market_id = ? "
            "AND status = 'aktiv'",
            (market.provider, market.provider_market_id),
        ).fetchall()
        updated = 0
        for setup_id, created_at in rows:
            duration_hours = None
            try:
                started = datetime.fromisoformat(created_at)
                resolved = market.resolved_at or datetime.now(UTC)
                duration_hours = round((resolved - started).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                pass
            self.connection.execute(
                """
                UPDATE shadow_setups SET
                    status = 'aufgelöst', resolved_at = ?, final_outcome = ?, final_yes_price = ?,
                    duration_hours = ?
                WHERE id = ?
                """,
                (now, market.winning_outcome, market.yes_price, duration_hours, setup_id),
            )
            updated += 1
        if updated:
            self.connection.commit()
        return updated

    def close(self) -> None:
        self.connection.close()
