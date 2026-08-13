from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from . import data_sources
from .migrations import current_schema_version, run_migrations
from .models import Market, ResolutionStatus, Signal
from .prediction.classification import classify_market
from .prediction.semantics import parse_market_proposition
from .providers.base import ProviderCapabilities
from .signals import PreviousSnapshot

if TYPE_CHECKING:
    from .prediction.semantics import ExtractedEvent

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
)


# Phase D: cheap, dependency-free entity extraction for the comparable-case
# scorer (D3). Deliberately a standalone implementation rather than reaching
# into semantics.py's private `_extract_actors` — Phase D only *consumes*
# Phase A/B/C's public functions (parse_market_proposition/classify_market),
# it does not modify or import their internals.
_ENTITY_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b")
_ENTITY_STOPWORDS = frozenset({"Will", "The", "A", "An", "This", "That", "Is", "Yes", "No"})


def _extract_entities(question: str, max_entities: int = 8) -> list[str]:
    """Naive proper-noun-run extraction from a market question, used only
    as a comparable-case entity-overlap signal — not a replacement for any
    Phase A/B/C NLP."""
    seen: set[str] = set()
    entities: list[str] = []
    for match in _ENTITY_RE.findall(question or ""):
        if match in _ENTITY_STOPWORDS or len(match) < 3:
            continue
        if match not in seen:
            seen.add(match)
            entities.append(match)
    return entities[:max_entities]


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

    # --- provider health tracking -----------------------------------------

    def save_provider_health(self, health: data_sources.ProviderHealth) -> None:
        """Save provider health metrics to the database."""
        row = data_sources.provider_health_to_row(health)
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            INSERT INTO provider_health (
                source_id, last_success, last_failure, last_failure_reason,
                last_http_status, last_latency_ms, consecutive_failures,
                data_age_seconds, items_fetched, parse_failures, last_check_timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                last_success = excluded.last_success,
                last_failure = excluded.last_failure,
                last_failure_reason = excluded.last_failure_reason,
                last_http_status = excluded.last_http_status,
                last_latency_ms = excluded.last_latency_ms,
                consecutive_failures = excluded.consecutive_failures,
                data_age_seconds = excluded.data_age_seconds,
                items_fetched = excluded.items_fetched,
                parse_failures = excluded.parse_failures,
                last_check_timestamp = excluded.last_check_timestamp
            """,
            row + (now,),
        )
        self.connection.commit()

    def get_provider_health(self, source_id: str) -> data_sources.ProviderHealth | None:
        """Load provider health metrics from the database."""
        row = self.connection.execute(
            """
            SELECT source_id, last_success, last_failure, last_failure_reason,
                   last_http_status, last_latency_ms, consecutive_failures,
                   data_age_seconds, items_fetched, parse_failures
            FROM provider_health
            WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return data_sources.row_to_provider_health(row)

    def get_all_provider_health(self) -> list[dict]:
        """Load every observed provider-health row for the API dashboard."""
        rows = self.connection.execute(
            """
            SELECT source_id, last_success, last_failure, last_failure_reason,
                   last_http_status, last_latency_ms, consecutive_failures,
                   data_age_seconds, items_fetched, parse_failures
            FROM provider_health
            ORDER BY source_id
            """
        ).fetchall()
        return [data_sources.row_to_provider_health(row).as_dict() for row in rows]

    def save_macro_snapshot(self, snapshot) -> None:
        """Persists the (series_id, observation_date, value) points behind a
        real (or realistically-mocked) providers/fred.py MacroSnapshot into
        macro_observations, with a fetch timestamp for freshness scoring.
        Best-effort/additive only — callers should not depend on this for
        correctness of the live forecast path (mirrors the fact that
        providers/coingecko.py's quant data is also fetched fresh on every
        call rather than read back from storage)."""
        now = datetime.now(UTC).isoformat()
        rows = [
            ("FEDFUNDS", snapshot.policy_rate_as_of.isoformat(), snapshot.policy_rate),
            ("CPIAUCSL_YOY", snapshot.as_of_date.isoformat(), snapshot.cpi_yoy),
            ("UNRATE", snapshot.as_of_date.isoformat(), snapshot.unemployment_rate),
        ]
        for series_id, observation_date, value in rows:
            self.connection.execute(
                """
                INSERT INTO macro_observations (series_id, observation_date, value, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(series_id, observation_date) DO UPDATE SET
                    value = excluded.value,
                    fetched_at = excluded.fetched_at
                """,
                (series_id, observation_date, value, now),
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

    def _upsert_market_row(self, market: Market) -> str:
        """Insert-or-update the `markets` row for one market and return its
        canonical `market_id`. Shared by `save()` (active scans) and
        `record_resolution()` (resolved-market ingestion) so a market's
        historical record survives independently of whether it's still
        returned by the provider's *active* market feed — a resolved market
        must never vanish from the historical knowledge base just because
        it dropped out of the current scan window."""
        now = datetime.now(UTC).isoformat()
        key = _row_key(market)

        # Phase C: taxonomy classification, computed at write time and
        # stored alongside (never in place of) the provider's raw
        # `category` string. `category` itself is left untouched so
        # history.py's comparability grouping and any historical rows keep
        # meaning exactly what they always meant.
        try:
            proposition = parse_market_proposition(market.question, market.description)
            classification = classify_market(market.question, market.description, proposition)
        except Exception:  # noqa: BLE001 - classification must never block a market write
            # Fall back to "unclassified" rather than losing the scan.
            classification = None
            proposition = None

        classified_category = classification.category if classification else None
        classification_confidence = classification.confidence if classification else None
        event_type = classification.event_type if classification else None
        proposition_json = json.dumps(proposition.as_dict()) if proposition else None
        deadline = proposition.deadline if proposition else None
        entities_json = json.dumps(_extract_entities(market.question))

        self.connection.execute(
            """
            INSERT INTO markets (
                market_id, provider, provider_market_id, condition_id, question, slug,
                category, tags, url, yes_token_id, no_token_id, start_date, end_date,
                event_id, description, outcomes, outcome_prices, resolved_at,
                resolution_status, winning_outcome, resolution_source, raw_data_hash,
                provider_data, first_seen_at, last_seen_at,
                classified_category, classification_confidence, event_type,
                proposition_json, entities_json, deadline
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                last_seen_at = excluded.last_seen_at,
                classified_category = excluded.classified_category,
                classification_confidence = excluded.classification_confidence,
                event_type = excluded.event_type,
                proposition_json = excluded.proposition_json,
                entities_json = excluded.entities_json,
                deadline = excluded.deadline
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
                classified_category,
                classification_confidence,
                event_type,
                proposition_json,
                entities_json,
                deadline,
            ),
        )
        # The stored market_id may differ from `key` for rows that pre-date
        # the provider-prefixed key format (migrated Phase-1 data); always
        # use the canonical value actually on the row so child-table
        # foreign keys stay consistent and no duplicate `markets` row is
        # created.
        return self.connection.execute(
            "SELECT market_id FROM markets WHERE provider = ? AND provider_market_id = ?",
            (market.provider, market.provider_market_id),
        ).fetchone()[0]

    def save(self, run_id: int, market_signals: list[tuple[Market, list[Signal]]]) -> int:
        """Persist markets, snapshots, price history and research signals for
        one scan batch. Returns the number of snapshots actually written
        (skipping unchanged snapshots unless `store_unchanged_snapshots`)."""
        now = datetime.now(UTC).isoformat()
        snapshots_written = 0

        for market, signals in market_signals:
            key = self._upsert_market_row(market)
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

    # --- Phase D: backfill classification/proposition on existing rows ----

    def backfill_classifications(self, only_missing: bool = True) -> int:
        """Re-run classify_market/parse_market_proposition over already-stored
        `markets` rows and populate classified_category/event_type/
        proposition_json/entities_json/deadline. Needed for rows written
        before migration 14 (or before Phase C's classifier existed at all)
        so find_comparable_cases() has real structured data for every
        historical row, not just newly-ingested ones. `only_missing=True`
        (default) only touches rows where proposition_json is still NULL;
        pass False to force a full re-classification pass. Returns the
        number of rows updated."""
        where = "WHERE proposition_json IS NULL" if only_missing else ""
        rows = self.connection.execute(
            f"SELECT market_id, question, description FROM markets {where}"
        ).fetchall()
        updated = 0
        for market_id, question, description in rows:
            question = question or ""
            try:
                proposition = parse_market_proposition(question, description)
                classification = classify_market(question, description, proposition)
            except Exception:  # noqa: BLE001 - never abort the backfill on one bad row
                classification = None
                proposition = None
            if classification is None or proposition is None:
                continue
            self.connection.execute(
                """
                UPDATE markets SET
                    classified_category = ?, classification_confidence = ?, event_type = ?,
                    proposition_json = ?, entities_json = ?, deadline = ?
                WHERE market_id = ?
                """,
                (
                    classification.category,
                    classification.confidence,
                    classification.event_type,
                    json.dumps(proposition.as_dict()),
                    json.dumps(_extract_entities(question)),
                    proposition.deadline,
                    market_id,
                ),
            )
            updated += 1
        self.connection.commit()
        return updated

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

        # Always upsert the `markets` row first — a resolved market must be
        # preserved in the historical knowledge base even if it was never
        # captured by a normal active-market scan, and even on a no-op
        # resolution update below (the markets upsert is itself idempotent).
        self._upsert_market_row(market)

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

    # --- public market flow (order book / trades / holders) --------------------

    def save_orderbook_snapshot(self, provider: str, provider_market_id: str, bids: list, asks: list) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            "INSERT INTO orderbook_snapshots (provider, provider_market_id, captured_at, bids_json, asks_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, provider_market_id, now, json.dumps(bids), json.dumps(asks)),
        )
        self.connection.commit()

    def save_public_trade_event(self, provider: str, provider_market_id: str, trade) -> bool:
        """Returns False (no-op) if this trade_hash was already stored for
        this market — trades are immutable once mined, so re-fetching the
        same window should not create duplicates."""
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO public_trade_events (provider, provider_market_id, trade_hash, captured_at,
                                              traded_at, side, outcome, price, size, wallet_address)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_market_id, trade_hash) DO NOTHING
            """,
            (
                provider, provider_market_id, trade.trade_hash, now,
                datetime.fromtimestamp(trade.traded_at_unix, tz=UTC).isoformat(),
                trade.side, trade.outcome, trade.price, trade.size, trade.wallet_address,
            ),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def save_public_wallet_positions(self, provider: str, provider_market_id: str, holders: list) -> None:
        now = datetime.now(UTC).isoformat()
        for h in holders:
            self.connection.execute(
                """
                INSERT INTO public_wallet_positions (provider, provider_market_id, captured_at,
                                                       wallet_address, outcome_index, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_market_id, captured_at, wallet_address, outcome_index) DO NOTHING
                """,
                (provider, provider_market_id, now, h.wallet_address, h.outcome_index, h.amount),
            )
        self.connection.commit()

    def save_market_flow_signal(self, provider: str, provider_market_id: str, status: str, net_flow: float | None, large_trade_ratio: float | None, price_move_without_evidence: bool, detail: str) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            INSERT INTO market_flow_signals (provider, provider_market_id, captured_at, status, net_flow,
                                              large_trade_ratio, price_move_without_evidence, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (provider, provider_market_id, now, status, net_flow, large_trade_ratio, int(price_move_without_evidence), detail),
        )
        self.connection.commit()

    def save_market_reliability_snapshot(self, market_id: str, level: str, score: float | None, detail: str) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            "INSERT INTO market_reliability_snapshots (market_id, captured_at, reliability_level, reliability_score, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (market_id, now, level, score, detail),
        )
        self.connection.commit()

    def save_manipulation_risk_event(self, market_id: str, risk_score: float, reasons: list, confidence: float) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            "INSERT INTO manipulation_risk_events (market_id, captured_at, risk_score, reasons_json, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (market_id, now, risk_score, json.dumps(reasons), confidence),
        )
        self.connection.commit()

    def latest_orderbook_snapshot(self, provider: str, provider_market_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT captured_at, bids_json, asks_json FROM orderbook_snapshots "
            "WHERE provider = ? AND provider_market_id = ? ORDER BY captured_at DESC LIMIT 1",
            (provider, provider_market_id),
        ).fetchone()
        if row is None:
            return None
        return {"captured_at": row[0], "bids": json.loads(row[1]), "asks": json.loads(row[2])}

    def recent_public_trades(self, provider: str, provider_market_id: str, limit: int = 100) -> list[dict]:
        rows = self.connection.execute(
            "SELECT trade_hash, traded_at, side, outcome, price, size, wallet_address FROM public_trade_events "
            "WHERE provider = ? AND provider_market_id = ? ORDER BY traded_at DESC LIMIT ?",
            (provider, provider_market_id, limit),
        ).fetchall()
        cols = ("trade_hash", "traded_at", "side", "outcome", "price", "size", "wallet_address")
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def latest_wallet_positions(self, provider: str, provider_market_id: str) -> list[dict]:
        row = self.connection.execute(
            "SELECT MAX(captured_at) FROM public_wallet_positions WHERE provider = ? AND provider_market_id = ?",
            (provider, provider_market_id),
        ).fetchone()
        if row is None or row[0] is None:
            return []
        rows = self.connection.execute(
            "SELECT wallet_address, outcome_index, amount FROM public_wallet_positions "
            "WHERE provider = ? AND provider_market_id = ? AND captured_at = ?",
            (provider, provider_market_id, row[0]),
        ).fetchall()
        cols = ("wallet_address", "outcome_index", "amount")
        return [dict(zip(cols, r, strict=True)) for r in rows]

    # --- shadow trading (simulation only — no real orders) ---------------------

    def save_shadow_trade(self, decision, market_id: str, source_snapshot_id: int | None, engine_version: str) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO shadow_trades (
                market_id, provider, provider_market_id, source_snapshot_id, created_at, direction,
                entry_market_price, independent_probability, expected_edge, confidence, opportunity_score,
                reliability_score, manipulation_risk, deadline_phase, assumed_stake, simulated_fee,
                simulated_slippage, reasons_json, blockers_json, status, engine_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_id, decision.provider, decision.provider_market_id, source_snapshot_id, now,
                decision.direction, decision.entry_market_price, decision.independent_probability,
                decision.expected_edge, decision.confidence, decision.opportunity_score,
                decision.reliability_score, decision.manipulation_risk, decision.deadline_phase,
                decision.assumed_stake, decision.simulated_fee, decision.simulated_slippage,
                json.dumps(list(decision.reasons)), json.dumps(list(decision.blockers)),
                decision.status, engine_version,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def activate_shadow_trade(self, shadow_trade_id: int) -> None:
        self.connection.execute("UPDATE shadow_trades SET status = 'active' WHERE id = ?", (shadow_trade_id,))
        self.connection.commit()

    def update_shadow_trade_lifecycle(self, shadow_trade_id: int, fields: dict) -> None:
        if not fields:
            return
        columns = ", ".join(f"{k} = ?" for k in fields)
        self.connection.execute(
            f"UPDATE shadow_trades SET {columns} WHERE id = ?", (*fields.values(), shadow_trade_id)
        )
        self.connection.commit()

    def close_shadow_trade(
        self, shadow_trade_id: int, final_resolution_status: str | None, final_outcome: str | None,
        simulated_pnl: float | None, roi: float | None, holding_hours: float | None, exit_reason: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """
            UPDATE shadow_trades SET status = 'closed', final_resolution_status = ?, final_outcome = ?,
                simulated_pnl = ?, roi = ?, holding_hours = ?, exit_reason = ?, exit_at = ?, closed_at = ?
            WHERE id = ?
            """,
            (final_resolution_status, final_outcome, simulated_pnl, roi, holding_hours, exit_reason, now, now, shadow_trade_id),
        )
        self.connection.commit()

    def active_shadow_trades(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM shadow_trades WHERE status IN ('candidate', 'active') ORDER BY created_at DESC"
        ).fetchall()
        cols = [d[0] for d in self.connection.execute("SELECT * FROM shadow_trades LIMIT 0").description]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    def all_shadow_trades(self, limit: int = 500) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM shadow_trades ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        cols = [d[0] for d in self.connection.execute("SELECT * FROM shadow_trades LIMIT 0").description]
        return [dict(zip(cols, r, strict=True)) for r in rows]

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
            SELECT id, response_json, input_tokens, output_tokens, created_at,
                   estimated_cost_usd, actual_cost_usd, final_status
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
            "estimated_cost_usd": row[5],
            "actual_cost_usd": row[6],
            "final_status": row[7],
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
        cached_input_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        actual_cost_usd: float | None = None,
        requested_model: str | None = None,
        final_status: str | None = None,
        total_attempts: int | None = None,
        repair_attempted: bool | None = None,
        total_input_tokens: int | None = None,
        total_output_tokens: int | None = None,
        total_estimated_cost_usd: float | None = None,
        total_actual_cost_usd: float | None = None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO ai_analysis_runs (
                analysis_type, market_id, model, prompt_version, context_hash, status,
                created_at, duration_ms, input_tokens, output_tokens, cached, error_code, response_json,
                cached_input_tokens, estimated_cost_usd, actual_cost_usd,
                requested_model, final_status, total_attempts, repair_attempted,
                total_input_tokens, total_output_tokens, total_estimated_cost_usd, total_actual_cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                cached_input_tokens,
                estimated_cost_usd,
                actual_cost_usd,
                requested_model,
                final_status,
                total_attempts,
                None if repair_attempted is None else int(repair_attempted),
                total_input_tokens,
                total_output_tokens,
                total_estimated_cost_usd,
                total_actual_cost_usd,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def record_ai_model_attempt(self, run_id: int, attempt) -> int:
        """Persists one `ai.status.ModelAttempt` — including attempts that
        were never actually sent (budget-blocked), so the full decision
        trail (main attempt, repair attempt, fallback-model escalation) is
        always reconstructable from the database alone."""
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO ai_model_attempts (
                run_id, attempt_number, is_repair, requested_model, actual_model, status,
                input_tokens, output_tokens, estimated_cost_usd, actual_cost_usd, duration_ms,
                error_detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, attempt.attempt_number, int(attempt.is_repair), attempt.requested_model,
                attempt.actual_model, attempt.status, attempt.input_tokens, attempt.output_tokens,
                attempt.estimated_cost_usd, attempt.actual_cost_usd, attempt.duration_ms,
                attempt.error_detail, now,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list_ai_model_attempts(self, run_id: int) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT attempt_number, is_repair, requested_model, actual_model, status,
                   input_tokens, output_tokens, estimated_cost_usd, actual_cost_usd, duration_ms, error_detail
            FROM ai_model_attempts WHERE run_id = ? ORDER BY attempt_number ASC
            """,
            (run_id,),
        ).fetchall()
        cols = (
            "attempt_number", "is_repair", "requested_model", "actual_model", "status",
            "input_tokens", "output_tokens", "estimated_cost_usd", "actual_cost_usd", "duration_ms", "error_detail",
        )
        return [dict(zip(cols, r, strict=True)) for r in rows]

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

    def save_prediction_snapshot(
        self, market_id: str, provider: str, provider_market_id: str, category: str | None,
        prediction_version: str, market_yes_probability: float | None, estimated_yes_probability: float | None,
        net_yes_edge: float | None, confidence_score: float, recommendation: str, comparable_sample_size: int,
        independent_probability: float | None = None, resolution_clarity: float | None = None,
        market_reliability_score: float | None = None, market_reliability_level: str | None = None,
        manipulation_risk_score: float | None = None, opportunity_score: float | None = None,
        deadline_phase: str | None = None, evidence_count: int | None = None,
        independent_confirmation_count: int | None = None, contradiction_present: bool | None = None,
        orderbook_imbalance: float | None = None, net_flow: float | None = None,
        wallet_concentration_score: float | None = None, reaction_lag_hours: float | None = None,
        submodel_estimates_json: str | None = None, warnings_json: str | None = None,
        engine_version: str | None = None, config_hash: str | None = None,
        # --- Phase N: shadow forecast snapshot fields (additive, PRE-resolution only) ---
        market_probability_at_forecast: float | None = None,
        blended_probability: float | None = None, calibrated_probability: float | None = None,
        confidence_calibration_status: str | None = None, forecast_status: str | None = None,
        models_used: str | None = None, divergence_verdict: str | None = None,
        # --- Block E Part 4: forecast-semantics fields flowing into the
        # forecast-history snapshot mechanism (migration 22, additive) ---
        model_hypothesis_probability: float | None = None,
        evidence_backed_probability: float | None = None,
        published_forecast_probability: float | None = None,
        forecast_maturity: str | None = None,
        evidence_strength: str | None = None,
        data_quality_composite_score: float | None = None,
        # --- Block G Part 4: persisted NO_FORECAST reason (additive,
        # migration 23) ---
        no_forecast_reason: str | None = None,
        data_gap_summary_json: str | None = None,
    ) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO prediction_snapshots (
                market_id, provider, provider_market_id, category, prediction_version, created_at,
                market_yes_probability, estimated_yes_probability, net_yes_edge, confidence_score,
                recommendation, comparable_sample_size, independent_probability, resolution_clarity,
                market_reliability_score, market_reliability_level, manipulation_risk_score, opportunity_score,
                deadline_phase, evidence_count, independent_confirmation_count, contradiction_present,
                orderbook_imbalance, net_flow, wallet_concentration_score, reaction_lag_hours,
                submodel_estimates_json, warnings_json, engine_version, config_hash,
                forecast_at, market_probability_at_forecast, blended_probability, calibrated_probability,
                confidence_calibration_status, forecast_status, models_used, divergence_verdict,
                model_hypothesis_probability, evidence_backed_probability, published_forecast_probability,
                forecast_maturity, evidence_strength, data_quality_composite_score,
                no_forecast_reason, data_gap_summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_id, provider, provider_market_id, category, prediction_version, now,
                market_yes_probability, estimated_yes_probability, net_yes_edge, confidence_score,
                recommendation, comparable_sample_size, independent_probability, resolution_clarity,
                market_reliability_score, market_reliability_level, manipulation_risk_score, opportunity_score,
                deadline_phase, evidence_count, independent_confirmation_count,
                int(contradiction_present) if contradiction_present is not None else None,
                orderbook_imbalance, net_flow, wallet_concentration_score, reaction_lag_hours,
                submodel_estimates_json, warnings_json, engine_version, config_hash,
                # forecast_at deliberately equals `now` (the same forecast-time write instant as
                # created_at) — no resolution/outcome data is ever read or written on this path.
                now, market_probability_at_forecast, blended_probability, calibrated_probability,
                confidence_calibration_status, forecast_status, models_used, divergence_verdict,
                model_hypothesis_probability, evidence_backed_probability, published_forecast_probability,
                forecast_maturity, evidence_strength, data_quality_composite_score,
                no_forecast_reason, data_gap_summary_json,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def save_extracted_event(
        self,
        provider: str,
        provider_market_id: str,
        title: str,
        event: ExtractedEvent,
        source: str | None = None,
        news_event_id: int | None = None,
        occurred_at: str | None = None,
    ) -> int:
        """Phase H: additive persistence of the structured event that
        `prediction.semantics.extract_event()` already computes during
        evidence scoring (see prediction/evidence.py's
        compute_independent_evidence). Reuses the migration-12 `events`
        table (extended in migration 15) rather than a parallel schema —
        pure storage, no causal/graph-traversal inference. Provenance
        (source, certainty, created_at, and the linking news_event_id /
        market) is recorded alongside every row."""
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            """
            INSERT INTO events (
                title, event_type, occurred_at, geographic_scope, source, source_url, created_at,
                actors_json, action, target, expected_time, status, source_type, certainty,
                provider, provider_market_id, news_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title, event.event_type, occurred_at or event.event_time, event.location,
                source or event.source, None, now,
                json.dumps(list(event.actors)), event.action, event.target, event.expected_time,
                event.status, event.source_type, event.certainty,
                provider, provider_market_id, news_event_id,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def cost_report(self, days: int = 7) -> dict:
        """Aggregate KI-cost figures for the CLI/API cost report. Groups by
        model so nano vs. mini usage — and their very different unit costs —
        stay visible instead of being averaged away.

        Extended after a live smoke test revealed that failed calls (bad
        JSON, schema mismatch, inconsistent numbers) were persisted
        identically to a plain "AI disabled" fallback, making real-but-
        rejected usage invisible. `by_status`/`attempts` below are built
        from `ai_model_attempts`, which records every individual call
        attempt (including ones never sent due to a budget block) with its
        own status, tokens, and cost — `total_actual_cost_usd` sums only
        the attempts that actually reported a cost, and is `None` (not
        `0.0`) when not a single attempt in the period ever reported one,
        so "no spend" and "spend unknown" never collapse into each other.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        # Legacy rows (pre-migration 9) have final_status = NULL; fall back
        # to the old "input_tokens IS NULL" heuristic for those so older
        # entries stay readable/interpretable instead of erroring out.
        by_model = self.connection.execute(
            """
            SELECT model,
                   COUNT(*) AS runs,
                   SUM(CASE WHEN cached = 0 THEN 1 ELSE 0 END) AS live_runs,
                   SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) AS cache_hits,
                   SUM(CASE WHEN actual_cost_usd IS NOT NULL THEN actual_cost_usd ELSE 0 END) AS total_actual_cost_usd,
                   SUM(CASE WHEN actual_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS runs_with_known_cost,
                   COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS total_output_tokens
            FROM ai_analysis_runs
            WHERE analysis_type = 'explain_recommendation' AND created_at >= ?
            GROUP BY model
            """,
            (cutoff,),
        ).fetchall()
        by_model_rows = []
        for model, runs, live_runs, cache_hits, total_cost, runs_with_known_cost, total_in, total_out in by_model:
            by_model_rows.append(
                {
                    "model": model, "runs": runs, "live_runs": live_runs, "cache_hits": cache_hits,
                    "total_actual_cost_usd": round(total_cost, 6),
                    "avg_actual_cost_usd": round(total_cost / runs_with_known_cost, 6) if runs_with_known_cost else None,
                    "total_input_tokens": total_in, "total_output_tokens": total_out,
                }
            )

        spent_today_row = self.connection.execute(
            "SELECT SUM(CASE WHEN actual_cost_usd IS NOT NULL THEN actual_cost_usd ELSE 0 END) FROM ai_analysis_runs "
            "WHERE analysis_type = 'explain_recommendation' AND created_at >= ?",
            (today_start,),
        ).fetchone()[0]
        spent_today = round(spent_today_row or 0.0, 6)

        fallback_count = self.connection.execute(
            "SELECT COUNT(*) FROM ai_analysis_runs "
            "WHERE analysis_type = 'explain_recommendation' AND created_at >= ? "
            "AND (final_status IS NOT NULL AND final_status != 'success' "
            "     OR (final_status IS NULL AND input_tokens IS NULL))",
            (cutoff,),
        ).fetchone()[0]

        by_status_rows = self.connection.execute(
            """
            SELECT COALESCE(final_status, 'unknown_legacy'), COUNT(*)
            FROM ai_analysis_runs
            WHERE analysis_type = 'explain_recommendation' AND created_at >= ?
            GROUP BY COALESCE(final_status, 'unknown_legacy')
            """,
            (cutoff,),
        ).fetchall()

        run_totals = self.connection.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN final_status = 'success' THEN 1 ELSE 0 END),
                SUM(COALESCE(total_attempts, 0)),
                SUM(CASE WHEN repair_attempted = 1 THEN 1 ELSE 0 END),
                SUM(CASE WHEN total_actual_cost_usd IS NOT NULL THEN total_actual_cost_usd ELSE 0 END),
                SUM(CASE WHEN total_actual_cost_usd IS NOT NULL THEN 1 ELSE 0 END),
                COALESCE(SUM(total_input_tokens), 0),
                COALESCE(SUM(total_output_tokens), 0)
            FROM ai_analysis_runs
            WHERE analysis_type = 'explain_recommendation' AND created_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        (
            total_runs, successful_runs, total_attempts_sum, runs_with_repair,
            total_cost_sum, runs_with_known_total_cost, total_in_sum, total_out_sum,
        ) = run_totals

        attempt_totals = self.connection.execute(
            """
            SELECT
                SUM(CASE WHEN is_repair = 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN is_repair = 1 THEN 1 ELSE 0 END),
                SUM(CASE WHEN actual_model IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN actual_model IS NULL THEN 1 ELSE 0 END)
            FROM ai_model_attempts a
            JOIN ai_analysis_runs r ON r.id = a.run_id
            WHERE r.analysis_type = 'explain_recommendation' AND a.created_at >= ?
            """,
            (cutoff,),
        ).fetchone()
        main_attempts, repair_attempts, sent_attempts, blocked_attempts = (v or 0 for v in attempt_totals)

        return {
            "period_days": days,
            "spent_today_usd": spent_today,
            "by_model": by_model_rows,
            "rule_based_fallback_runs": fallback_count,
            "by_status": {status: count for status, count in by_status_rows},
            "totals": {
                "runs": total_runs or 0,
                "successful_runs": successful_runs or 0,
                "fallback_runs": (total_runs or 0) - (successful_runs or 0),
                "total_attempts": total_attempts_sum or 0,
                "runs_with_repair_attempted": runs_with_repair or 0,
                "total_input_tokens": total_in_sum,
                "total_output_tokens": total_out_sum,
                "total_actual_cost_usd": round(total_cost_sum, 6) if runs_with_known_total_cost else None,
                "runs_with_unknown_cost": (total_runs or 0) - (runs_with_known_total_cost or 0),
            },
            "attempts": {
                "main_attempts_sent": main_attempts,
                "repair_attempts_sent": repair_attempts,
                "attempts_sent": sent_attempts,
                "attempts_blocked_before_send": blocked_attempts,
            },
        }

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

    # --- Phase O: Claims API --- #
    # These methods support the claims.py module for claim extraction,
    # deduplication, and verification.

    def save_claim(self, claim: object) -> bool:
        """Save an extracted claim to the database.
        
        Returns True if the claim was newly inserted, False if it already existed.
        """
        from polymarketpulse.claims import Claim
        
        if not isinstance(claim, Claim):
            return False
        
        now = datetime.now(UTC).isoformat()
        try:
            self.connection.execute(
                """
                INSERT INTO claims (
                    claim_id, subject, predicate, object, speaker, source_id,
                    source_url, timestamp, verification_status, confidence,
                    entities_json, location, raw_reference, event_type, direction,
                    resolution_step, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO NOTHING
                """,
                (
                    claim.claim_id, claim.subject, claim.predicate, claim.object, claim.speaker,
                    claim.source_id, claim.source_url,
                    claim.timestamp.isoformat() if claim.timestamp else None,
                    claim.verification_status, claim.confidence,
                    json.dumps(list(claim.entities)) if claim.entities else None,
                    claim.location, claim.raw_reference, claim.event_type, claim.direction,
                    getattr(claim, "resolution_step", None), now,
                ),
            )
            self.connection.commit()
            return self.connection.total_changes > 0
        except (sqlite3.Error, ValueError):
            return False

    def save_claim_group(self, group: object) -> bool:
        """Save a claim group (deduplicated claims).
        
        Returns True if the group was newly inserted, False if it already existed.
        """
        from polymarketpulse.claims import ClaimGroup
        
        if not isinstance(group, ClaimGroup):
            return False
        
        now = datetime.now(UTC).isoformat()
        try:
            self.connection.execute(
                """
                INSERT INTO claim_groups (
                    claim_id, canonical_claim_id, republishing_sources_json,
                    independent_sources, confirmation_count, verification_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO NOTHING
                """,
                (
                    group.claim_id, group.canonical_claim.claim_id,
                    json.dumps(list(group.republishing_sources)) if group.republishing_sources else None,
                    group.independent_sources, group.confirmation_count,
                    group.verification_status, now,
                ),
            )
            self.connection.commit()
            return self.connection.total_changes > 0
        except (sqlite3.Error, ValueError):
            return False

    def save_claim_source(self, claim_id: str, source_id: str, source_url: str | None = None, timestamp: str | None = None) -> None:
        """Record that a source contributed to a claim (for deduplication tracking)."""
        try:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO claim_sources (claim_id, source_id, source_url, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (claim_id, source_id, source_url, timestamp),
            )
            self.connection.commit()
        except sqlite3.Error:
            pass

    def save_research_run(self, record: dict) -> int | None:
        """Persist one Observability record for a real research run against
        one market (see research_runner.py). `record` is the plain dict
        produced by ResearchRunObservability.as_dict(); unknown/missing keys
        default to None/0 so this never raises on a partial record. Returns
        the new row id, or None on a storage error (never raises — this is
        observability infrastructure, must never break a real research run)."""
        try:
            cur = self.connection.execute(
                """
                INSERT INTO research_runs (
                    run_at, provider, provider_market_id, question, trigger,
                    sources_requested, sources_fetched, sources_accepted, sources_rejected,
                    claims_extracted, claims_deduplicated, claims_linked, claims_rejected,
                    independent_source_groups, primary_source_count,
                    data_gaps_before, data_gaps_after,
                    evidence_before, evidence_after,
                    model_hypothesis_before, model_hypothesis_after,
                    evidence_backed_before, evidence_backed_after,
                    published_forecast_before, published_forecast_after,
                    final_status, duration_ms, cost_usd, detail_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("run_at"), record.get("provider"), record.get("provider_market_id"),
                    record.get("question"), record.get("trigger"),
                    record.get("sources_requested", 0), record.get("sources_fetched", 0),
                    record.get("sources_accepted", 0), record.get("sources_rejected", 0),
                    record.get("claims_extracted", 0), record.get("claims_deduplicated", 0),
                    record.get("claims_linked", 0), record.get("claims_rejected", 0),
                    record.get("independent_source_groups", 0), record.get("primary_source_count", 0),
                    record.get("data_gaps_before"), record.get("data_gaps_after"),
                    record.get("evidence_before"), record.get("evidence_after"),
                    record.get("model_hypothesis_before"), record.get("model_hypothesis_after"),
                    record.get("evidence_backed_before"), record.get("evidence_backed_after"),
                    record.get("published_forecast_before"), record.get("published_forecast_after"),
                    record.get("final_status"), record.get("duration_ms"),
                    record.get("cost_usd", 0.0),
                    json.dumps(record.get("detail")) if record.get("detail") is not None else None,
                ),
            )
            self.connection.commit()
            return cur.lastrowid
        except sqlite3.Error:
            return None

    def get_research_runs(self, provider_market_id: str | None = None, limit: int = 50) -> list[dict]:
        """Real, persisted research-run history — the API/CLI-retrievable
        Observability surface (not just log lines)."""
        try:
            if provider_market_id:
                rows = self.connection.execute(
                    "SELECT * FROM research_runs WHERE provider_market_id = ? ORDER BY run_at DESC LIMIT ?",
                    (provider_market_id, limit),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT * FROM research_runs ORDER BY run_at DESC LIMIT ?", (limit,)
                ).fetchall()
            cols = [d[0] for d in self.connection.execute("SELECT * FROM research_runs LIMIT 0").description]
            return [dict(zip(cols, row, strict=True)) for row in rows]
        except sqlite3.Error:
            return []

    def save_claim_market_link(
        self, claim_id: str, provider: str, provider_market_id: str, claim_type: str,
    ) -> None:
        """Real claim -> market link with an explicit claim_type
        classification (DIRECT_RESOLUTION / PATH_STEP / QUANTITATIVE_SIGNAL
        / CONTEXT) -- closes the documented gap (evaluation.py's
        evaluate_source_performance) that no table linked a claim to the
        market it was actually fetched for."""
        try:
            self.connection.execute(
                """
                INSERT INTO claim_market_links (claim_id, provider, provider_market_id, claim_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(claim_id, provider, provider_market_id) DO UPDATE SET claim_type = excluded.claim_type
                """,
                (claim_id, provider, provider_market_id, claim_type, datetime.now(UTC).isoformat()),
            )
            self.connection.commit()
        except sqlite3.Error:
            pass

    def get_claim_market_links(self, provider: str, provider_market_id: str) -> list[dict]:
        """Real claims linked to one specific market, joined with their
        full claim data -- the real input evidence.py/world_state.py use
        to fold structured claims into the same probability math the
        article-based evidence already uses."""
        try:
            rows = self.connection.execute(
                """
                SELECT c.claim_id, c.subject, c.predicate, c.source_id, c.source_url,
                       c.timestamp, c.verification_status, c.confidence, c.direction,
                       c.resolution_step, cml.claim_type
                FROM claim_market_links cml
                JOIN claims c ON c.claim_id = cml.claim_id
                WHERE cml.provider = ? AND cml.provider_market_id = ?
                """,
                (provider, provider_market_id),
            ).fetchall()
            cols = ("claim_id", "subject", "predicate", "source_id", "source_url", "timestamp",
                    "verification_status", "confidence", "direction", "resolution_step", "claim_type")
            return [dict(zip(cols, row, strict=True)) for row in rows]
        except sqlite3.Error:
            return []

    def save_counter_evidence(
        self, claim_id: str, contradicts_claim_id: str,
        source_id: str | None = None, source_url: str | None = None,
        confidence: float | None = None,
    ) -> None:
        """Record that one claim contradicts another."""
        try:
            now = datetime.now(UTC).isoformat()
            self.connection.execute(
                """
                INSERT INTO claim_counter_evidence (
                    claim_id, contradicts_claim_id, source_id, source_url, timestamp, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (claim_id, contradicts_claim_id, source_id, source_url, now, confidence, now),
            )
            self.connection.commit()
        except sqlite3.Error:
            pass

    def mark_claim_superseded(self, old_claim_id: str, new_claim_id: str) -> None:
        """Phase D: explicit replacement -- only set by real
        dedup/replacement logic that has identified a genuine successor
        claim, never inferred from age alone."""
        try:
            self.connection.execute(
                "UPDATE claims SET superseded_by = ? WHERE claim_id = ?", (new_claim_id, old_claim_id)
            )
            self.connection.commit()
        except sqlite3.Error:
            pass

    # --- Phase C: Event/Entity/Relation write path --- #
    # migration 12 (events.py) defined the real schema but, until now, had
    # no writer anywhere in the codebase -- real events/relations existed
    # only as empty, read-wired scaffolding. These methods are the minimal,
    # idempotent write path that closes that gap.

    def save_entity(self, canonical_name: str, entity_type: str, geographic_scope: str | None = None) -> int | None:
        """Idempotent by canonical_name (schema UNIQUE constraint). Returns
        the entity's id, existing or newly created."""
        try:
            self.connection.execute(
                "INSERT INTO entities (canonical_name, entity_type, geographic_scope) VALUES (?, ?, ?) "
                "ON CONFLICT(canonical_name) DO NOTHING",
                (canonical_name.strip().lower(), entity_type, geographic_scope),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT id FROM entities WHERE canonical_name = ?", (canonical_name.strip().lower(),)
            ).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    def save_event(
        self, title: str, event_type: str, occurred_at: str | None = None,
        geographic_scope: str | None = None, source: str | None = None, source_url: str | None = None,
    ) -> int | None:
        """Not schema-unique (events has no natural key), so dedup manually
        on (title, source_url, occurred_at) -- repeated research runs for
        the same real data point must not grow this table unboundedly."""
        try:
            row = self.connection.execute(
                "SELECT id FROM events WHERE title = ? AND source_url IS ? AND occurred_at IS ?",
                (title, source_url, occurred_at),
            ).fetchone()
            if row:
                return row[0]
            cur = self.connection.execute(
                "INSERT INTO events (title, event_type, occurred_at, geographic_scope, source, source_url, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, event_type, occurred_at, geographic_scope, source, source_url, datetime.now(UTC).isoformat()),
            )
            self.connection.commit()
            return cur.lastrowid
        except sqlite3.Error:
            return None

    def save_event_entity_link(self, event_id: int, entity_id: int, role: str | None = None) -> None:
        try:
            self.connection.execute(
                "INSERT INTO event_entity_links (event_id, entity_id, role) VALUES (?, ?, ?) "
                "ON CONFLICT(event_id, entity_id, role) DO NOTHING",
                (event_id, entity_id, role),
            )
            self.connection.commit()
        except sqlite3.Error:
            pass

    def save_event_relation(
        self, *, source_event_id: int | None, source_entity_id: int | None, target_entity_id: int | None,
        target_provider: str, target_provider_market_id: str, relation_type: str, direction: str,
        evidence_tier: str, detail: str, strength: float | None = None, confidence: float | None = None,
        time_lag_hours: float | None = None, geographic_scope: str | None = None,
        evidence_count: int = 1, source_quality: str | None = None,
        valid_from: str | None = None, valid_until: str | None = None,
    ) -> int | None:
        """Dedup on (target market, relation_type, detail) -- `detail`
        carries the real predicate/timestamp of the underlying data point,
        so a genuinely new fact naturally produces a new row while a
        repeated research run for the same fact does not."""
        from .events import validate_relation_tier

        try:
            validate_relation_tier(relation_type, evidence_tier)
        except ValueError:
            return None
        try:
            row = self.connection.execute(
                "SELECT id FROM event_relations WHERE target_provider = ? AND target_provider_market_id = ? "
                "AND relation_type = ? AND detail = ?",
                (target_provider, target_provider_market_id, relation_type, detail),
            ).fetchone()
            if row:
                return row[0]
            cur = self.connection.execute(
                """
                INSERT INTO event_relations (
                    source_event_id, source_entity_id, target_entity_id, target_provider,
                    target_provider_market_id, relation_type, direction, strength, evidence_tier,
                    confidence, time_lag_hours, geographic_scope, evidence_count, source_quality,
                    valid_from, valid_until, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_event_id, source_entity_id, target_entity_id, target_provider,
                    target_provider_market_id, relation_type, direction, strength, evidence_tier,
                    confidence, time_lag_hours, geographic_scope, evidence_count, source_quality,
                    valid_from, valid_until, detail, datetime.now(UTC).isoformat(),
                ),
            )
            self.connection.commit()
            return cur.lastrowid
        except sqlite3.Error:
            return None

    def save_social_signal(self, signal: dict, market_ids: tuple[str, ...] = (), entity_ids: tuple[int, ...] = ()) -> bool:
        """Persist an auditable public discovery signal and graph links.
        Missing source references are rejected; this is intentionally not a
        claim writer and therefore cannot move forecast evidence."""
        required = ("signal_id", "source_type", "provider", "canonical_url", "detected_at", "summary", "raw_reference", "origin_cluster", "signal_status", "verification_status")
        if any(not signal.get(name) for name in required):
            return False
        try:
            self.connection.execute("""INSERT INTO social_signals
                (signal_id,source_type,provider,account,canonical_url,detected_at,published_at,summary,raw_reference,origin_cluster,signal_status,verification_status,confidence,category,event_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(signal_id) DO UPDATE SET
                signal_status=excluded.signal_status, verification_status=excluded.verification_status, confidence=excluded.confidence""", (
                signal["signal_id"], signal["source_type"], signal["provider"], signal.get("account"), signal["canonical_url"], signal["detected_at"], signal.get("published_at"), signal["summary"], signal["raw_reference"], signal["origin_cluster"], signal["signal_status"], signal["verification_status"], signal.get("confidence", 0.0), signal.get("category"), signal.get("event_id"),
            ))
            for market_id in market_ids:
                self.connection.execute("INSERT INTO social_signal_markets(signal_id,market_id,match_method,confidence) VALUES(?,?,?,?) ON CONFLICT(signal_id,market_id) DO NOTHING", (signal["signal_id"], market_id, "GRAPH_LINK", signal.get("match_confidence", 1.0)))
            for entity_id in entity_ids:
                self.connection.execute("INSERT INTO social_signal_entities(signal_id,entity_id) VALUES(?,?) ON CONFLICT(signal_id,entity_id) DO NOTHING", (signal["signal_id"], entity_id))
            self.connection.commit()
            return True
        except sqlite3.Error:
            return False

    def get_social_signals(self, market_id: str, limit: int = 10) -> list[dict]:
        try:
            rows = self.connection.execute("""SELECT s.signal_id,s.source_type,s.provider,s.account,s.canonical_url,s.detected_at,s.published_at,s.summary,s.raw_reference,s.origin_cluster,s.signal_status,s.verification_status,s.confidence,s.category
                FROM social_signals s JOIN social_signal_markets m ON m.signal_id=s.signal_id
                WHERE m.market_id=? ORDER BY s.detected_at DESC LIMIT ?""", (market_id, limit)).fetchall()
            cols = ("signal_id","source_type","provider","account","canonical_url","detected_at","published_at","summary","raw_reference","origin_cluster","signal_status","verification_status","confidence","category")
            return [dict(zip(cols, row, strict=True)) for row in rows]
        except sqlite3.Error:
            return []

    def close(self) -> None:
        self.connection.close()
