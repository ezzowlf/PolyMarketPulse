from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ..storage import Storage
from .schemas import MarketContext

# Hard caps. The AI never sees more than this, regardless of how much
# history/news/signals actually exist in the database.
MAX_PRICE_HISTORY_POINTS = 20
MAX_NEWS_ITEMS = 5
MAX_SIGNALS = 8
MAX_COMPARABLE_MARKETS = 5
MAX_DESCRIPTION_CHARS = 800


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_market_context(storage: Storage, market_id: str) -> MarketContext | None:
    """Builds a bounded, pre-filtered context for one market. Returns None
    if the market doesn't exist — callers must treat that as "insufficient
    context" (HTTP 424), never fall back to guessing."""
    row = storage.connection.execute(
        """
        SELECT market_id, provider, question, description, category, resolution_status, end_date
        FROM markets WHERE market_id = ?
        """,
        (market_id,),
    ).fetchone()
    if row is None:
        return None
    _mid, provider, question, description, category, resolution_status, end_date = row

    latest = storage.connection.execute(
        """
        SELECT yes_price, no_price, spread, liquidity, volume_24h
        FROM market_snapshots WHERE market_id = ? ORDER BY captured_at DESC LIMIT 1
        """,
        (market_id,),
    ).fetchone()
    yes_price, no_price, spread, liquidity, volume_24h = latest if latest else (None, None, None, None, None)

    days_to_resolution = None
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            days_to_resolution = round((end_dt - datetime.now(UTC)).total_seconds() / 86400, 2)
        except ValueError:
            pass

    history_rows = storage.connection.execute(
        """
        SELECT captured_at, yes_price, opportunity_score FROM market_snapshots
        WHERE market_id = ? ORDER BY captured_at DESC LIMIT ?
        """,
        (market_id, MAX_PRICE_HISTORY_POINTS),
    ).fetchall()
    price_history = [
        {"captured_at": r[0], "yes_price": r[1], "opportunity_score": r[2]} for r in reversed(history_rows)
    ]

    dq_row = storage.connection.execute(
        """
        SELECT score, issues_json FROM data_quality_reports
        WHERE provider = ? AND provider_market_id = (
            SELECT provider_market_id FROM markets WHERE market_id = ?
        )
        ORDER BY captured_at DESC LIMIT 1
        """,
        (provider, market_id),
    ).fetchone()
    data_quality_score = dq_row[0] if dq_row else None
    data_quality_issues = json.loads(dq_row[1]) if dq_row and dq_row[1] else []

    signal_rows = storage.connection.execute(
        """
        SELECT id, signal_type, score, reasons, captured_at, status FROM research_signals
        WHERE provider = ? AND provider_market_id = (
            SELECT provider_market_id FROM markets WHERE market_id = ?
        )
        ORDER BY captured_at DESC LIMIT ?
        """,
        (provider, market_id, MAX_SIGNALS),
    ).fetchall()
    research_signals = [
        {
            "id": f"signal:{r[0]}",
            "signal_type": r[1],
            "score": r[2],
            "reasons": r[3],
            "captured_at": r[4],
            "status": r[5],
        }
        for r in signal_rows
    ]

    news_rows = storage.connection.execute(
        """
        SELECT n.id, n.title, n.source, n.published_at, l.confidence, l.match_reason
        FROM news_market_links l JOIN news_events n ON n.id = l.news_event_id
        WHERE l.provider = ? AND l.provider_market_id = (
            SELECT provider_market_id FROM markets WHERE market_id = ?
        )
        ORDER BY l.confidence DESC LIMIT ?
        """,
        (provider, market_id, MAX_NEWS_ITEMS),
    ).fetchall()
    relevant_news = [
        {
            "id": f"news:{r[0]}",
            "title": r[1],
            "source": r[2],
            "published_at": r[3],
            "confidence": r[4],
            "match_reason": r[5],
        }
        for r in news_rows
    ]

    comparable_rows = storage.connection.execute(
        """
        SELECT provider_a, provider_market_id_a, provider_b, provider_market_id_b, text_similarity
        FROM market_matches
        WHERE status = 'confirmed'
          AND (
            (provider_a = ? AND provider_market_id_a = (SELECT provider_market_id FROM markets WHERE market_id = ?))
            OR (provider_b = ? AND provider_market_id_b = (SELECT provider_market_id FROM markets WHERE market_id = ?))
          )
        LIMIT ?
        """,
        (provider, market_id, provider, market_id, MAX_COMPARABLE_MARKETS),
    ).fetchall()
    comparable_confirmed_markets = [
        {
            "id": f"match:{i}",
            "provider_a": r[0],
            "provider_b": r[2],
            "text_similarity": r[4],
        }
        for i, r in enumerate(comparable_rows)
    ]

    source_ids = (
        [f"snapshot:{p['captured_at']}" for p in price_history[-3:]]
        + [s["id"] for s in research_signals]
        + [n["id"] for n in relevant_news]
        + [c["id"] for c in comparable_confirmed_markets]
    )

    return MarketContext(
        market_id=market_id,
        provider=provider,
        question=_truncate(question, 300) or "",
        description=_truncate(description, MAX_DESCRIPTION_CHARS),
        category=category,
        resolution_status=resolution_status or "unresolved",
        yes_price=yes_price,
        no_price=no_price,
        spread=spread,
        liquidity=liquidity,
        volume_24h=volume_24h,
        days_to_resolution=days_to_resolution,
        price_history=price_history,
        data_quality_score=data_quality_score,
        data_quality_issues=data_quality_issues,
        research_signals=research_signals,
        relevant_news=relevant_news,
        comparable_confirmed_markets=comparable_confirmed_markets,
        source_ids=source_ids,
    )


def context_hash(context: MarketContext) -> str:
    """Stable hash used both for cache lookups and as a version marker
    stored alongside each AI run — changes whenever the underlying data the
    model saw would have changed."""
    canonical = json.dumps(context.model_dump(exclude_none=True), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
