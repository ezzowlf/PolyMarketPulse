"""Backfill real historical Polymarket YES-price history for already-
resolved markets, from Polymarket's public CLOB /prices-history endpoint.

Part of the Proof-of-Edge task: local market_snapshots/price_history have
zero rows before resolution for any of the 126 resolved markets in
market_resolutions, so there is no real historical price to benchmark
independent_probability against. This script fetches real, timestamped
price points (never fabricated) for as many resolved markets as reasonably
possible within a polite request budget, and writes them additively into
the new `polymarket_price_history` table (migration 20).

Usage:
    python scripts/backfill_polymarket_price_history.py [--limit N] [--db PATH]

Safety:
- Purely additive: INSERT OR IGNORE into a brand-new table only. Never
  deletes or updates any existing row/table.
- Paced requests (default 0.25s between calls) with exponential backoff on
  HTTP 429.
- Every price point's captured_at is the REAL historical timestamp from the
  API's "t" field (converted to UTC ISO8601) — never fetch time.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarketpulse.providers.polymarket import fetch_price_history
from polymarketpulse.storage import Storage

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "polymarketpulse.db"
REQUEST_PACE_SECONDS = 0.25
MAX_RETRIES_ON_429 = 3


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def fetch_with_backoff(token_id: str, start_ts: int, end_ts: int) -> list[tuple[int, float]] | None:
    """Wraps fetch_price_history with 429 backoff. fetch_price_history
    itself swallows HTTP errors into None, so we can't distinguish 429 from
    other failures at that layer without duplicating request logic — this
    is an accepted limitation; a bare None is retried up to
    MAX_RETRIES_ON_429 times with increasing delay, which is a safe,
    generically-polite response to any transient failure including 429."""
    delay = 1.0
    for attempt in range(MAX_RETRIES_ON_429 + 1):
        result = fetch_price_history(token_id, start_ts, end_ts)
        if result is not None:
            return result
        if attempt < MAX_RETRIES_ON_429:
            time.sleep(delay)
            delay *= 2
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=126)
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB))
    args = parser.parse_args()

    db_path = Path(args.db)
    store = Storage(db_path)
    conn = store.connection

    rows = conn.execute(
        """
        SELECT m.market_id, m.condition_id, m.yes_token_id, m.start_date,
               mr.resolved_at
        FROM market_resolutions mr
        JOIN markets m ON mr.provider = m.provider AND mr.provider_market_id = m.provider_market_id
        WHERE mr.status = 'resolved'
          AND m.yes_token_id IS NOT NULL AND m.yes_token_id != ''
        ORDER BY mr.resolved_at
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()

    print(f"Eligible resolved markets with yes_token_id: {len(rows)}")

    total_points = 0
    markets_with_data = 0
    markets_empty = 0
    markets_failed = 0
    fetched_at = datetime.now(UTC).isoformat()

    # Live-tested against clob.polymarket.com/prices-history: the endpoint
    # rejects (HTTP 400 "interval is too long") any startTs/endTs window
    # wider than roughly 2-3 weeks, REGARDLESS of the requested market's
    # actual lifetime or the fidelity parameter. So instead of requesting
    # the market's full open-to-resolution window (which 400s for anything
    # open more than ~3 weeks), we request the WINDOW_DAYS immediately
    # before resolution. This is also exactly the window Part 2's
    # forecast_time selection rule needs (a snapshot some days before
    # resolution), so it costs nothing for the backtest.
    WINDOW_DAYS = 14

    for i, (market_id, condition_id, token_id, start_date, resolved_at) in enumerate(rows):
        end_dt = _parse_dt(resolved_at)
        if end_dt is None:
            markets_failed += 1
            continue
        open_dt = _parse_dt(start_date)
        window_start = end_dt - timedelta(days=WINDOW_DAYS)
        if open_dt is not None and open_dt > window_start:
            window_start = open_dt
        start_ts = int(window_start.timestamp())
        end_ts = int((end_dt + timedelta(hours=1)).timestamp())

        points = fetch_with_backoff(token_id, start_ts, end_ts)
        time.sleep(REQUEST_PACE_SECONDS)

        if points is None:
            markets_failed += 1
            print(f"[{i + 1}/{len(rows)}] {market_id}: FETCH FAILED")
            continue
        if len(points) == 0:
            markets_empty += 1
            print(f"[{i + 1}/{len(rows)}] {market_id}: 0 points")
            continue

        rows_to_insert = [
            (
                market_id,
                condition_id,
                token_id,
                datetime.fromtimestamp(t, tz=UTC).isoformat(),
                p,
                "polymarket_backfill",
                fetched_at,
            )
            for t, p in points
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO polymarket_price_history
                (market_id, condition_id, token_id, captured_at, yes_price, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        conn.commit()
        total_points += len(points)
        markets_with_data += 1
        print(f"[{i + 1}/{len(rows)}] {market_id}: {len(points)} points")

    print("\n--- Backfill summary ---")
    print(f"Markets attempted:        {len(rows)}")
    print(f"Markets with real data:   {markets_with_data}")
    print(f"Markets with empty hist:  {markets_empty}")
    print(f"Markets failed to fetch:  {markets_failed}")
    print(f"Total price points saved: {total_points}")

    store.connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
