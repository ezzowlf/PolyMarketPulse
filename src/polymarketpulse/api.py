from __future__ import annotations

import functools
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .ai import service as ai_service
from .ai.client import (
    AIContextError,
    AIDisabledError,
    AINetworkError,
    AIRateLimitError,
    AIResponseError,
    AITimeoutError,
)
from .ai.schemas import (
    AIAnalysisResponse,
    AIStatusResponse,
    AskRequest,
    CompareRequest,
    ExplainRecommendationResponse,
)
from .config import Settings
from .providers.registry import create_provider, list_provider_names
from .stats import compute_signal_stats
from .storage import Storage

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Run migrations exactly once at process start, not per-request — the
    # dashboard fires several parallel requests per page load, and
    # re-running migration bookkeeping on every one just adds SQLite lock
    # contention for no benefit.
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    storage.close()
    yield


app = FastAPI(
    title="PolymarketPulse API",
    description=(
        "Read-only REST API over locally stored prediction-market research data. "
        "No wallet, no order placement, no real-money actions of any kind."
    ),
    version="0.3.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1", "*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def get_storage():
    settings = Settings.load()
    storage = Storage(
        settings.database_path,
        store_unchanged_snapshots=settings.store_unchanged_snapshots,
        auto_migrate=False,
    )
    try:
        yield storage
    finally:
        storage.close()


def _market_row_to_dict(row: dict) -> dict:
    row = dict(row)
    for json_field in ("tags", "outcomes", "outcome_prices", "provider_data"):
        if json_field in row and isinstance(row[json_field], str):
            try:
                row[json_field] = json.loads(row[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
    return row


MARKET_COLUMNS = (
    "market_id",
    "provider",
    "provider_market_id",
    "condition_id",
    "question",
    "slug",
    "category",
    "tags",
    "url",
    "yes_token_id",
    "no_token_id",
    "start_date",
    "end_date",
    "event_id",
    "description",
    "outcomes",
    "outcome_prices",
    "resolved_at",
    "resolution_status",
    "winning_outcome",
    "resolution_source",
    "first_seen_at",
    "last_seen_at",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "polymarketpulse-api"}


@app.get("/providers")
def providers(storage: Storage = Depends(get_storage)) -> list[dict]:
    settings = Settings.load()
    result = []
    for name in list_provider_names():
        provider = create_provider(name, timeout=settings.request_timeout)
        result.append({"name": name, **provider.capabilities.as_dict()})
        provider.close()
    return result


@app.get("/provider/{name}")
def provider_detail(name: str, storage: Storage = Depends(get_storage)) -> dict:
    settings = Settings.load()
    try:
        provider = create_provider(name, timeout=settings.request_timeout)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    info = {"name": provider.name, **provider.capabilities.as_dict()}
    provider.close()

    row = storage.connection.execute(
        "SELECT COUNT(*) FROM markets WHERE provider = ?", (name,)
    ).fetchone()
    last_run = storage.connection.execute(
        "SELECT started_at, finished_at, status, markets_fetched FROM scanner_runs "
        "WHERE provider = ? ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone()
    info["markets_stored"] = row[0] if row else 0
    info["last_run"] = (
        {"started_at": last_run[0], "finished_at": last_run[1], "status": last_run[2], "markets_fetched": last_run[3]}
        if last_run
        else None
    )
    return info


@app.get("/markets")
def markets(
    provider: str | None = None,
    category: str | None = None,
    search: str | None = None,
    min_liquidity: float | None = None,
    min_volume: float | None = None,
    limit: int = 50,
    offset: int = 0,
    storage: Storage = Depends(get_storage),
) -> dict:
    limit = max(1, min(limit, 500))
    conditions = []
    params: list[Any] = []
    if provider:
        conditions.append("m.provider = ?")
        params.append(provider)
    if category:
        conditions.append("m.category = ?")
        params.append(category)
    if search:
        conditions.append("m.question LIKE ?")
        params.append(f"%{search}%")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Latest snapshot per market for liquidity/volume/score filtering & display.
    query = f"""
        SELECT m.market_id, m.provider, m.provider_market_id, m.question, m.slug, m.category,
               m.url, m.end_date, m.resolution_status,
               ls.yes_price, ls.liquidity, ls.volume_24h, ls.spread, ls.opportunity_score
        FROM markets m
        LEFT JOIN (
            SELECT ms1.* FROM market_snapshots ms1
            JOIN (
                SELECT market_id, MAX(captured_at) AS latest FROM market_snapshots GROUP BY market_id
            ) ms2 ON ms1.market_id = ms2.market_id AND ms1.captured_at = ms2.latest
        ) ls ON ls.market_id = m.market_id
        {where}
        ORDER BY ls.opportunity_score DESC NULLS LAST
        LIMIT ? OFFSET ?
    """
    params_with_paging = [*params, limit, offset]
    try:
        rows = storage.connection.execute(query, params_with_paging).fetchall()
    except sqlite3.OperationalError:  # SQLite < 3.30 lacks NULLS LAST
        query_fallback = query.replace("DESC NULLS LAST", "DESC")
        rows = storage.connection.execute(query_fallback, params_with_paging).fetchall()

    columns = (
        "market_id",
        "provider",
        "provider_market_id",
        "question",
        "slug",
        "category",
        "url",
        "end_date",
        "resolution_status",
        "yes_price",
        "liquidity",
        "volume_24h",
        "spread",
        "opportunity_score",
    )
    items = [dict(zip(columns, row, strict=True)) for row in rows]
    if min_liquidity is not None:
        items = [i for i in items if (i["liquidity"] or 0) >= min_liquidity]
    if min_volume is not None:
        items = [i for i in items if (i["volume_24h"] or 0) >= min_volume]

    total = storage.connection.execute(f"SELECT COUNT(*) FROM markets m {where}", params).fetchone()[0]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/market/{market_id}")
def market_detail(market_id: str, storage: Storage = Depends(get_storage)) -> dict:
    row = storage.connection.execute(
        f"SELECT {', '.join(MARKET_COLUMNS)} FROM markets WHERE market_id = ?", (market_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Market not found")
    market = _market_row_to_dict(dict(zip(MARKET_COLUMNS, row, strict=True)))

    latest_snapshot = storage.connection.execute(
        """
        SELECT captured_at, yes_price, no_price, best_bid, best_ask, liquidity, volume_24h,
               volume_total, spread, one_day_change, opportunity_score
        FROM market_snapshots WHERE market_id = ? ORDER BY captured_at DESC LIMIT 1
        """,
        (market_id,),
    ).fetchone()
    snapshot_cols = (
        "captured_at", "yes_price", "no_price", "best_bid", "best_ask", "liquidity",
        "volume_24h", "volume_total", "spread", "one_day_change", "opportunity_score",
    )
    market["latest"] = dict(zip(snapshot_cols, latest_snapshot, strict=True)) if latest_snapshot else None

    signal_rows = storage.connection.execute(
        """
        SELECT id, captured_at, signal_type, score, reasons, status
        FROM research_signals WHERE provider = ? AND provider_market_id = ?
        ORDER BY captured_at DESC LIMIT 50
        """,
        (market["provider"], market["provider_market_id"]),
    ).fetchall()
    market["signals"] = [
        dict(zip(("id", "captured_at", "signal_type", "score", "reasons", "status"), r, strict=True))
        for r in signal_rows
    ]

    news_rows = storage.connection.execute(
        """
        SELECT n.id, n.title, n.source, n.published_at, l.confidence, l.match_reason
        FROM news_market_links l JOIN news_events n ON n.id = l.news_event_id
        WHERE l.provider = ? AND l.provider_market_id = ?
        ORDER BY n.published_at DESC LIMIT 20
        """,
        (market["provider"], market["provider_market_id"]),
    ).fetchall()
    market["news"] = [
        dict(zip(("id", "title", "source", "published_at", "confidence", "match_reason"), r, strict=True))
        for r in news_rows
    ]

    from .opportunities import compute_opportunity

    latest = market.get("latest") or {}
    market_row_for_opportunity = {
        "market_id": market["market_id"], "provider": market["provider"], "provider_market_id": market["provider_market_id"],
        "question": market["question"], "category": market["category"], "url": market["url"],
        "end_date": market["end_date"], "first_seen_at": market["first_seen_at"], "last_seen_at": market["last_seen_at"],
        "yes_price": latest.get("yes_price"), "liquidity": latest.get("liquidity"),
        "volume_24h": latest.get("volume_24h"), "spread": latest.get("spread"),
    }
    market["opportunity"] = compute_opportunity(storage, market_row_for_opportunity)

    return market


@app.get("/signals")
def signals(
    signal_type: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    limit: int = 50,
    storage: Storage = Depends(get_storage),
) -> list[dict]:
    conditions = []
    params: list[Any] = []
    if signal_type:
        conditions.append("rs.signal_type = ?")
        params.append(signal_type)
    if provider:
        conditions.append("rs.provider = ?")
        params.append(provider)
    if status:
        conditions.append("rs.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(max(1, min(limit, 500)))

    rows = storage.connection.execute(
        f"""
        SELECT rs.id, rs.provider, rs.provider_market_id, rs.captured_at, rs.signal_type,
               rs.score, rs.reasons, rs.status, m.question, m.url, m.market_id
        FROM research_signals rs
        LEFT JOIN markets m ON m.provider = rs.provider AND m.provider_market_id = rs.provider_market_id
        {where}
        ORDER BY rs.captured_at DESC LIMIT ?
        """,
        params,
    ).fetchall()
    cols = ("id", "provider", "provider_market_id", "captured_at", "signal_type", "score", "reasons", "status", "question", "url", "market_id")
    return [dict(zip(cols, r, strict=True)) for r in rows]


@app.get("/signal/{signal_id}")
def signal_detail(signal_id: int, storage: Storage = Depends(get_storage)) -> dict:
    row = storage.connection.execute(
        """
        SELECT id, provider, provider_market_id, captured_at, signal_type, score, reasons,
               subfactors_json, origin_yes_price, forecast_probability, data_quality_flag, status
        FROM research_signals WHERE id = ?
        """,
        (signal_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    cols = (
        "id", "provider", "provider_market_id", "captured_at", "signal_type", "score", "reasons",
        "subfactors", "origin_yes_price", "forecast_probability", "data_quality_flag", "status",
    )
    result = dict(zip(cols, row, strict=True))
    try:
        result["subfactors"] = json.loads(result["subfactors"])
    except (json.JSONDecodeError, TypeError):
        pass

    evaluation = storage.connection.execute(
        """
        SELECT final_outcome, correct, simulated_pnl_per_unit, hold_duration_hours,
               max_favorable_excursion, max_adverse_excursion
        FROM signal_evaluations WHERE signal_id = ?
        """,
        (signal_id,),
    ).fetchone()
    if evaluation:
        eval_cols = ("final_outcome", "correct", "simulated_pnl_per_unit", "hold_duration_hours",
                     "max_favorable_excursion", "max_adverse_excursion")
        result["evaluation"] = dict(zip(eval_cols, evaluation, strict=True))
    else:
        result["evaluation"] = None
    return result


@app.get("/stats")
def stats(storage: Storage = Depends(get_storage)) -> dict:
    return compute_signal_stats(storage.connection).as_dict()


@app.get("/news")
def news(
    market_id: str | None = None,
    provider: str | None = None,
    limit: int = 50,
    storage: Storage = Depends(get_storage),
) -> list[dict]:
    if market_id:
        rows = storage.connection.execute(
            """
            SELECT n.id, n.title, n.source, n.source_url, n.published_at,
                   l.confidence, l.match_reason, l.matched_terms
            FROM news_market_links l JOIN news_events n ON n.id = l.news_event_id
            JOIN markets m ON m.provider = l.provider AND m.provider_market_id = l.provider_market_id
            WHERE m.market_id = ?
            ORDER BY n.published_at DESC LIMIT ?
            """,
            (market_id, max(1, min(limit, 500))),
        ).fetchall()
        cols = ("id", "title", "source", "source_url", "published_at", "confidence", "match_reason", "matched_terms")
        return [dict(zip(cols, r, strict=True)) for r in rows]

    conditions = []
    params: list[Any] = []
    if provider:
        conditions.append(
            "id IN (SELECT news_event_id FROM news_market_links WHERE provider = ?)"
        )
        params.append(provider)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(max(1, min(limit, 500)))
    rows = storage.connection.execute(
        f"SELECT id, title, source, source_url, published_at FROM news_events {where} "
        "ORDER BY published_at DESC LIMIT ?",
        params,
    ).fetchall()
    cols = ("id", "title", "source", "source_url", "published_at")
    return [dict(zip(cols, r, strict=True)) for r in rows]


@app.get("/history/{market_id}")
def history(market_id: str, storage: Storage = Depends(get_storage)) -> list[dict]:
    rows = storage.connection.execute(
        """
        SELECT captured_at, yes_price, no_price, liquidity, volume_24h, spread, opportunity_score
        FROM market_snapshots WHERE market_id = ? ORDER BY captured_at ASC
        """,
        (market_id,),
    ).fetchall()
    cols = ("captured_at", "yes_price", "no_price", "liquidity", "volume_24h", "spread", "opportunity_score")
    return [dict(zip(cols, r, strict=True)) for r in rows]


@app.get("/watchlist")
def get_watchlist(storage: Storage = Depends(get_storage)) -> list[dict]:
    """Enriched with live prediction data (edge/confidence/deadline/status)
    and change-since-added, so the watchlist is a work area, not just a
    list of favorites."""
    from .opportunities import compute_opportunity

    items = storage.list_watchlist()
    for item in items:
        row = storage.connection.execute(
            "SELECT market_id, provider, provider_market_id, question, category, url, "
            "end_date, first_seen_at, last_seen_at FROM markets WHERE provider = ? AND provider_market_id = ?",
            (item["provider"], item["provider_market_id"]),
        ).fetchone()
        if row is None:
            item["opportunity"] = None
            continue
        snap = storage.connection.execute(
            "SELECT yes_price, liquidity, volume_24h, spread FROM market_snapshots WHERE market_id = ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (row[0],),
        ).fetchone()
        market_row = {
            "market_id": row[0], "provider": row[1], "provider_market_id": row[2], "question": row[3],
            "category": row[4], "url": row[5], "end_date": row[6], "first_seen_at": row[7], "last_seen_at": row[8],
            "yes_price": snap[0] if snap else None, "liquidity": snap[1] if snap else None,
            "volume_24h": snap[2] if snap else None, "spread": snap[3] if snap else None,
        }
        item["opportunity"] = compute_opportunity(storage, market_row)
    return items


@app.post("/watchlist")
def add_to_watchlist(
    payload: dict = Body(...),
    storage: Storage = Depends(get_storage),
) -> dict:
    provider = payload.get("provider")
    provider_market_id = payload.get("provider_market_id")
    if not provider or not provider_market_id:
        raise HTTPException(status_code=422, detail="provider and provider_market_id are required")
    item_id = storage.add_watchlist_item(
        provider,
        provider_market_id,
        payload.get("note"),
        payload.get("alert_rules"),
        tags=payload.get("tags"),
        rating=payload.get("rating"),
        group=payload.get("group"),
        virtual_position=payload.get("virtual_position"),
    )
    return {"id": item_id, "status": "saved"}


@app.delete("/watchlist/{item_id}")
def delete_from_watchlist(item_id: int, storage: Storage = Depends(get_storage)) -> dict:
    removed = storage.remove_watchlist_item(item_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return {"status": "deleted"}


@app.get("/calendar")
def calendar(days_ahead: int = 30, storage: Storage = Depends(get_storage)) -> list[dict]:
    rows = storage.connection.execute(
        """
        SELECT market_id, question, provider, end_date, resolution_status
        FROM markets
        WHERE end_date IS NOT NULL AND resolution_status != 'resolved'
        ORDER BY end_date ASC LIMIT 200
        """
    ).fetchall()
    cols = ("market_id", "question", "provider", "end_date", "resolution_status")
    return [dict(zip(cols, r, strict=True)) for r in rows]


@app.get("/heatmap")
def heatmap(storage: Storage = Depends(get_storage)) -> list[dict]:
    rows = storage.connection.execute(
        """
        SELECT m.market_id, m.question, m.category, ls.liquidity, ls.opportunity_score,
               ls.one_day_change, ls.volume_24h
        FROM markets m
        JOIN (
            SELECT ms1.* FROM market_snapshots ms1
            JOIN (SELECT market_id, MAX(captured_at) AS latest FROM market_snapshots GROUP BY market_id) ms2
            ON ms1.market_id = ms2.market_id AND ms1.captured_at = ms2.latest
        ) ls ON ls.market_id = m.market_id
        """
    ).fetchall()
    cols = ("market_id", "question", "category", "liquidity", "opportunity_score", "one_day_change", "volume_24h")
    return [dict(zip(cols, r, strict=True)) for r in rows]


@app.get("/home")
def home(storage: Storage = Depends(get_storage)) -> dict:
    """Die neue Startseite: keine Datenbankübersicht, sondern eine kurze,
    verständliche Tageszusammenfassung plus maximal fünf hervorgehobene
    Märkte. Absichtlich knapp gehalten — der Rest der Plattform bleibt für
    alle, die tiefer graben wollen."""
    now = datetime.now(UTC)
    since_yesterday = (now - timedelta(hours=24)).isoformat()

    active_shadow_count = storage.connection.execute(
        "SELECT COUNT(*) FROM shadow_setups WHERE status = 'aktiv'"
    ).fetchone()[0]
    new_shadow_count = storage.connection.execute(
        "SELECT COUNT(*) FROM shadow_setups WHERE created_at >= ?", (since_yesterday,)
    ).fetchone()[0]
    news_count = storage.connection.execute(
        "SELECT COUNT(*) FROM news_events WHERE fetched_at >= ?", (since_yesterday,)
    ).fetchone()[0]
    soon_count = storage.connection.execute(
        "SELECT COUNT(*) FROM markets WHERE end_date IS NOT NULL AND resolution_status = 'unresolved' "
        "AND end_date <= ?",
        ((now + timedelta(hours=48)).isoformat(),),
    ).fetchone()[0]

    top_setups = storage.list_shadow_setups(status="aktiv", limit=5)
    highlights = []
    for setup in top_setups[:5]:
        latest = storage.connection.execute(
            "SELECT yes_price, opportunity_score FROM market_snapshots WHERE market_id = ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (setup["market_id"],),
        ).fetchone()
        yes_price, research_score = latest if latest else (None, None)
        price_change = None
        if setup["origin_yes_price"] is not None and yes_price is not None:
            price_change = round(yes_price - setup["origin_yes_price"], 4)
        latest_news = storage.connection.execute(
            "SELECT n.title, n.published_at FROM news_market_links l "
            "JOIN news_events n ON n.id = l.news_event_id "
            "WHERE l.provider = ? AND l.provider_market_id = ? ORDER BY n.published_at DESC LIMIT 1",
            (setup["provider"], setup["provider_market_id"]),
        ).fetchone()
        days_left = None
        if setup["end_date"]:
            try:
                days_left = round((datetime.fromisoformat(setup["end_date"]) - now).total_seconds() / 86400, 1)
            except ValueError:
                pass

        highlights.append(
            {
                "shadow_setup_id": setup["id"],
                "market_id": setup["market_id"],
                "frage": setup["question"],
                "url": setup["url"],
                "aktueller_preis": yes_price,
                "veraenderung_seit_erkennung": price_change,
                "research_score": research_score,
                "shadow_score": setup["score"],
                "wichtigste_gruende": setup["warum_interessant"][:3],
                "wichtigste_risiken": setup["warum_nicht"][:3] or setup["was_fehlt"][:2],
                "letzte_nachricht": (
                    {"titel": latest_news[0], "veroeffentlicht_am": latest_news[1]} if latest_news else None
                ),
                "tage_bis_resolution": days_left,
            }
        )

    return {
        "heute": {
            "maerkte_mit_hoher_aufmerksamkeit": active_shadow_count,
            "neue_shadow_setups": new_shadow_count,
            "wichtige_nachrichten": news_count,
            "maerkte_vor_entscheidung": soon_count,
        },
        "besonders_interessant": highlights,
        "generiert_am": now.isoformat(),
    }


@app.get("/shadow-setups")
def shadow_setups(status: str | None = None, limit: int = 20, storage: Storage = Depends(get_storage)) -> list[dict]:
    return storage.list_shadow_setups(status=status, limit=limit)


@app.get("/shadow-setup/{setup_id}")
def shadow_setup_detail(setup_id: int, storage: Storage = Depends(get_storage)) -> dict:
    setup = storage.get_shadow_setup(setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="Shadow-Setup nicht gefunden")

    price_history = storage.connection.execute(
        "SELECT captured_at, yes_price FROM price_history WHERE market_id = ? ORDER BY captured_at ASC",
        (setup["market_id"],),
    ).fetchall()
    news = storage.connection.execute(
        "SELECT n.title, n.source, n.published_at, l.confidence FROM news_market_links l "
        "JOIN news_events n ON n.id = l.news_event_id "
        "WHERE l.provider = ? AND l.provider_market_id = ? ORDER BY n.published_at DESC",
        (setup["provider"], setup["provider_market_id"]),
    ).fetchall()

    setup["preisverlauf_seit_erkennung"] = [
        {"zeitpunkt": r[0], "yes_preis": r[1]} for r in price_history if r[0] >= setup["created_at"]
    ]
    setup["nachrichten_seitdem"] = [
        {"titel": r[0], "quelle": r[1], "veroeffentlicht_am": r[2], "relevanz": r[3]}
        for r in news
        if r[2] is None or r[2] >= setup["created_at"]
    ]
    return setup


@app.get("/analytics")
def analytics(storage: Storage = Depends(get_storage)) -> dict:
    status = storage.status()
    signal_stats = compute_signal_stats(storage.connection)
    return {**status, "signal_stats": signal_stats.as_dict()}


@app.get("/settings")
def get_settings() -> dict:
    settings = Settings.load()
    return {
        "environment": settings.environment,
        "default_provider": settings.default_provider,
        "scan_limit": settings.scan_limit,
        "store_unchanged_snapshots": settings.store_unchanged_snapshots,
        "news_enabled": settings.news_enabled,
        "telegram_enabled": settings.telegram_enabled,
        # Never expose token/chat id/API-key values, even masked — presence
        # only, exactly like ai_ready already does for the AI layer.
        "ai": {
            "enabled": settings.ai_enabled,
            "api_key_present": bool(settings.openai_api_key),
            "ready": settings.ai_ready,
            "model": settings.openai_model,
            "fallback_model": settings.openai_fallback_model,
            "escalation_enabled": settings.openai_escalation_enabled,
            "max_cost_per_analysis_usd": settings.openai_max_cost_per_analysis_usd,
            "daily_budget_usd": settings.openai_daily_budget_usd,
        },
        "thresholds": {
            "min_liquidity": settings.min_liquidity,
            "min_volume_24h": settings.min_volume_24h,
        },
        # Note for the settings page: these values come from .env and are
        # read-only in the browser by design — changing them requires
        # editing .env and restarting, never a silently-ignored form field.
        "editable_in_browser": False,
    }


@app.get("/quality")
def quality(provider: str | None = None, storage: Storage = Depends(get_storage)) -> list[dict]:
    return storage.latest_quality_reports(provider=provider)


@app.get("/performance")
def performance(storage: Storage = Depends(get_storage)) -> dict:
    from .performance import compute_performance

    return compute_performance(storage.connection).as_dict()


@app.get("/simulation")
def simulation(limit: int = 100, storage: Storage = Depends(get_storage)) -> list[dict]:
    rows = storage.connection.execute(
        """
        SELECT rs.id, rs.provider, rs.provider_market_id, rs.signal_type, rs.captured_at,
               se.origin_yes_price, se.final_outcome, se.correct, se.simulated_pnl_per_unit,
               se.hold_duration_hours, se.max_favorable_excursion, se.max_adverse_excursion,
               m.question
        FROM signal_evaluations se
        JOIN research_signals rs ON rs.id = se.signal_id
        LEFT JOIN markets m ON m.provider = rs.provider AND m.provider_market_id = rs.provider_market_id
        ORDER BY se.evaluated_at DESC LIMIT ?
        """,
        (max(1, min(limit, 1000)),),
    ).fetchall()
    cols = (
        "signal_id", "provider", "provider_market_id", "signal_type", "captured_at",
        "origin_yes_price", "final_outcome", "correct", "simulated_pnl_per_unit",
        "hold_duration_hours", "max_favorable_excursion", "max_adverse_excursion", "question",
    )
    return [dict(zip(cols, r, strict=True)) for r in rows]


@app.get("/resolutions")
def resolutions(provider: str | None = None, status: str | None = None, storage: Storage = Depends(get_storage)) -> list[dict]:
    conditions = []
    params: list[Any] = []
    if provider:
        conditions.append("mr.provider = ?")
        params.append(provider)
    if status:
        conditions.append("mr.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = storage.connection.execute(
        f"""
        SELECT mr.provider, mr.provider_market_id, mr.resolved_at, mr.winning_outcome,
               mr.final_yes_price, mr.final_no_price, mr.resolution_source, mr.status,
               mr.detected_at, m.question
        FROM market_resolutions mr
        LEFT JOIN markets m ON m.provider = mr.provider AND m.provider_market_id = mr.provider_market_id
        {where}
        ORDER BY mr.resolved_at DESC
        """,
        params,
    ).fetchall()
    cols = (
        "provider", "provider_market_id", "resolved_at", "winning_outcome", "final_yes_price",
        "final_no_price", "resolution_source", "status", "detected_at", "question",
    )
    return [dict(zip(cols, r, strict=True)) for r in rows]


@app.get("/providers/status")
def providers_status(storage: Storage = Depends(get_storage)) -> list[dict]:
    settings = Settings.load()
    result = []
    for name in list_provider_names():
        provider = create_provider(name, timeout=settings.request_timeout)
        capabilities = provider.capabilities.as_dict()
        provider.close()

        market_count = storage.connection.execute(
            "SELECT COUNT(*) FROM markets WHERE provider = ?", (name,)
        ).fetchone()[0]
        resolution_count = storage.connection.execute(
            "SELECT COUNT(*) FROM market_resolutions WHERE provider = ?", (name,)
        ).fetchone()[0]
        last_run = storage.connection.execute(
            """
            SELECT started_at, finished_at, status, markets_fetched, duration_ms, error_details
            FROM scanner_runs WHERE provider = ? ORDER BY id DESC LIMIT 1
            """,
            (name,),
        ).fetchone()
        recent_errors = storage.connection.execute(
            "SELECT COUNT(*) FROM scanner_runs WHERE provider = ? AND status = 'failed'", (name,)
        ).fetchone()[0]
        avg_duration = storage.connection.execute(
            "SELECT AVG(duration_ms) FROM scanner_runs WHERE provider = ? AND status = 'completed'", (name,)
        ).fetchone()[0]

        result.append(
            {
                "name": name,
                "capabilities": capabilities,
                "markets_stored": market_count,
                "resolutions_recorded": resolution_count,
                "recent_failed_runs": recent_errors,
                "average_run_duration_ms": avg_duration,
                "last_run": (
                    {
                        "started_at": last_run[0],
                        "finished_at": last_run[1],
                        "status": last_run[2],
                        "markets_fetched": last_run[3],
                        "duration_ms": last_run[4],
                        "error_details": last_run[5],
                    }
                    if last_run
                    else None
                ),
            }
        )
    return result


@app.get("/search")
def search(q: str, limit: int = 20, storage: Storage = Depends(get_storage)) -> dict:
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=422, detail="Query must be at least 2 characters")
    return storage.search(q.strip(), limit=max(1, min(limit, 100)))


@app.get("/compare")
def compare(min_similarity: float = 0.35, storage: Storage = Depends(get_storage)) -> list[dict]:
    """Cross-provider price-divergence observations for candidate market
    matches. Purely observational — never a trading instruction, and never
    a claim that two markets are the same question unless a human has
    confirmed the match (`status == 'confirmed'`)."""
    from .matching import compute_divergence, find_candidate_matches

    rows = storage.connection.execute(
        """
        SELECT DISTINCT provider FROM markets
        """
    ).fetchall()
    providers_present = [r[0] for r in rows]
    if len(providers_present) < 2:
        return []

    all_markets: dict[str, list] = {}
    for provider_name in providers_present:
        market_rows = storage.connection.execute(
            f"SELECT {', '.join(MARKET_COLUMNS)} FROM markets WHERE provider = ?", (provider_name,)
        ).fetchall()
        all_markets[provider_name] = [
            _row_to_market(dict(zip(MARKET_COLUMNS, r, strict=True)), storage) for r in market_rows
        ]

    results: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    provider_names = list(all_markets.keys())
    for i, provider_a in enumerate(provider_names):
        for provider_b in provider_names[i + 1 :]:
            pair_key = (provider_a, provider_b)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            candidates = find_candidate_matches(
                all_markets[provider_a], all_markets[provider_b], min_text_similarity=min_similarity
            )
            for candidate in candidates[:20]:
                divergence = compute_divergence(candidate)
                results.append(
                    {
                        "provider_a": divergence.market_a.provider,
                        "question_a": divergence.market_a.question,
                        "yes_price_a": divergence.yes_price_a,
                        "provider_b": divergence.market_b.provider,
                        "question_b": divergence.market_b.question,
                        "yes_price_b": divergence.yes_price_b,
                        "divergence": divergence.divergence,
                        "text_similarity": candidate.text_similarity,
                        "status": divergence.status,
                    }
                )
    results.sort(key=lambda r: r["divergence"] or 0, reverse=True)
    return results


def _row_to_market(row: dict, storage: Storage):
    from .models import Market, ResolutionStatus

    latest = storage.connection.execute(
        "SELECT yes_price FROM market_snapshots WHERE market_id = ? ORDER BY captured_at DESC LIMIT 1",
        (row["market_id"],),
    ).fetchone()
    return Market(
        provider=row["provider"],
        provider_market_id=row["provider_market_id"],
        condition_id=row["condition_id"] or "",
        question=row["question"],
        slug=row["slug"],
        category=row["category"],
        yes_price=latest[0] if latest else None,
        resolution_status=ResolutionStatus(row["resolution_status"]) if row["resolution_status"] else ResolutionStatus.UNRESOLVED,
        url=row["url"],
    )


@app.get("/history/full/{market_id}")
def history_full(market_id: str, storage: Storage = Depends(get_storage)) -> dict:
    """Snapshot history plus derived price analytics (moving averages,
    volatility, trend reversals) — every field traceable to `market_snapshots`."""
    from .price_analytics import PricePoint, compute_price_analytics

    rows = storage.connection.execute(
        """
        SELECT captured_at, yes_price, liquidity, volume_24h, spread, opportunity_score
        FROM market_snapshots WHERE market_id = ? ORDER BY captured_at ASC
        """,
        (market_id,),
    ).fetchall()
    points = [
        PricePoint(
            captured_at=r[0], yes_price=r[1], liquidity=r[2], volume_24h=r[3], spread=r[4], opportunity_score=r[5]
        )
        for r in rows
    ]
    analytics_result = compute_price_analytics(points)
    return {
        "market_id": market_id,
        "points": [
            {
                "captured_at": p.captured_at,
                "yes_price": p.yes_price,
                "liquidity": p.liquidity,
                "volume_24h": p.volume_24h,
                "spread": p.spread,
                "opportunity_score": p.opportunity_score,
            }
            for p in points
        ],
        "analytics": analytics_result.as_dict(),
    }


@app.get("/explain/{market_id}")
def explain(market_id: str, mode: str = "movement", storage: Storage = Depends(get_storage)) -> dict:
    from .explain import (
        explain_market_movement,
        relevant_news_for_market,
        signals_before_movement,
        similar_markets,
    )

    handlers = {
        "movement": explain_market_movement,
        "news": relevant_news_for_market,
        "signals": signals_before_movement,
        "similar": similar_markets,
    }
    handler = handlers.get(mode, explain_market_movement)
    return handler(storage.connection, market_id).as_dict()


def _handle_ai_errors(func):
    """Maps our internal AI exception types onto stable HTTP status codes.
    Error messages here are already redacted by the ai.client layer — never
    pass through a raw upstream exception string."""

    @functools.wraps(func)
    def _wrapped(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AIDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except AIContextError as exc:
            raise HTTPException(status_code=424, detail=str(exc)) from exc
        except AIRateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except AITimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except AINetworkError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except AIResponseError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return _wrapped


@app.get("/ai/status")
def ai_status() -> AIStatusResponse:
    settings = Settings.load()
    reason = None
    if not settings.ai_enabled:
        reason = "POLYMARKETPULSE_AI_ENABLED is false"
    elif not settings.openai_api_key:
        reason = "OPENAI_API_KEY is not configured"
    return AIStatusResponse(
        enabled=settings.ai_enabled,
        ready=settings.ai_ready,
        model=settings.openai_model,
        cache_ttl_seconds=settings.ai_cache_ttl_seconds,
        reason=reason,
    )


@app.post("/ai/explain-market/{market_id}")
@_handle_ai_errors
def ai_explain_market(market_id: str, storage: Storage = Depends(get_storage)) -> AIAnalysisResponse:
    settings = Settings.load()
    return ai_service.explain_market(storage, settings, market_id)


@app.post("/ai/explain-signal/{signal_id}")
@_handle_ai_errors
def ai_explain_signal(signal_id: int, storage: Storage = Depends(get_storage)) -> AIAnalysisResponse:
    settings = Settings.load()
    return ai_service.explain_signal(storage, settings, signal_id)


@app.post("/ai/analyze-news/{market_id}")
@_handle_ai_errors
def ai_analyze_news(market_id: str, storage: Storage = Depends(get_storage)) -> AIAnalysisResponse:
    settings = Settings.load()
    return ai_service.analyze_news_for_market(storage, settings, market_id)


@app.post("/ai/compare")
@_handle_ai_errors
def ai_compare(payload: CompareRequest, storage: Storage = Depends(get_storage)) -> AIAnalysisResponse:
    settings = Settings.load()
    return ai_service.compare_markets(storage, settings, payload.market_ids)


@app.post("/ai/ask")
@_handle_ai_errors
def ai_ask(payload: AskRequest, storage: Storage = Depends(get_storage)) -> AIAnalysisResponse:
    settings = Settings.load()
    return ai_service.ask_research_question(storage, settings, payload.question, payload.market_id)


@app.get("/prediction/{market_id}")
@_handle_ai_errors
def prediction(market_id: str, storage: Storage = Depends(get_storage)) -> dict:
    """The binding statistical prediction only — no AI call, no cost. This
    is what GPT-5 nano is only ever allowed to explain, never invent."""
    return ai_service.get_prediction(storage, market_id).as_dict()


@app.get("/ai/explain-recommendation/{market_id}")
@_handle_ai_errors
def ai_explain_recommendation(market_id: str, storage: Storage = Depends(get_storage)) -> ExplainRecommendationResponse:
    """Cached-first: returns the market/NO_BET/YES/NO analysis, calling
    GPT-5 nano only if there is no valid cached explanation for the current
    prediction/data snapshot."""
    settings = Settings.load()
    return ai_service.explain_recommendation(storage, settings, market_id)


@app.post("/ai/explain-recommendation/{market_id}/recompute")
@_handle_ai_errors
def ai_explain_recommendation_recompute(
    market_id: str, storage: Storage = Depends(get_storage)
) -> ExplainRecommendationResponse:
    """Forces a fresh analysis, bypassing the cache — still subject to the
    same cost budget and validation as any other call."""
    settings = Settings.load()
    return ai_service.explain_recommendation(storage, settings, market_id, force_recompute=True)


@app.get("/ai/cost-report")
def ai_cost_report(days: int = 7, storage: Storage = Depends(get_storage)) -> dict:
    return storage.cost_report(days=days)


@app.get("/backtest")
def backtest(category: str | None = None, min_train_size: int = 5, storage: Storage = Depends(get_storage)) -> dict:
    from .backtest import run_backtest

    return run_backtest(storage.connection, category=category, min_train_size=min_train_size).as_dict()


@app.get("/evaluation")
def evaluation(storage: Storage = Depends(get_storage)) -> dict:
    from .evaluation import evaluate_predictions

    return evaluate_predictions(storage.connection).as_dict()


@app.get("/opportunities")
def opportunities(
    min_edge: float | None = None, min_confidence: float | None = None, category: str | None = None,
    max_deadline_hours: float | None = None, require_price: bool = False,
    sort: str = "opportunity_score", limit: int = 300, storage: Storage = Depends(get_storage),
) -> list[dict]:
    """The ranked 'what's interesting right now' list — Prediction Engine V2
    output translated into a product-facing status + composite score, never
    just raw |edge|. See opportunities.py for the ranking rationale."""
    from .opportunities import list_opportunities

    items = list_opportunities(storage, limit=limit)
    if require_price:
        items = [o for o in items if o["market_yes_probability"] is not None]
    if min_edge is not None:
        items = [o for o in items if o["net_yes_edge"] is not None and abs(o["net_yes_edge"]) >= min_edge]
    if min_confidence is not None:
        items = [o for o in items if o["confidence_score"] >= min_confidence]
    if category:
        items = [o for o in items if o["category"] == category]
    if max_deadline_hours is not None:
        items = [o for o in items if o["deadline_hours"] is not None and 0 <= o["deadline_hours"] <= max_deadline_hours]

    sort_keys = {
        "opportunity_score": lambda o: o["opportunity_score"],
        "edge": lambda o: abs(o["net_yes_edge"] or 0),
        "confidence": lambda o: o["confidence_score"],
        "deadline": lambda o: o["deadline_hours"] if o["deadline_hours"] is not None else float("inf"),
        "liquidity": lambda o: o["liquidity"] or 0,
        "volume": lambda o: o["volume_24h"] or 0,
        "last_seen": lambda o: o["last_seen_at"] or "",
    }
    key = sort_keys.get(sort, sort_keys["opportunity_score"])
    reverse = sort != "deadline"
    items.sort(key=key, reverse=reverse)
    return items


@app.get("/command-center")
def command_center(storage: Storage = Depends(get_storage)) -> dict:
    """The new Startseite's data source: counts + a handful of curated,
    prioritized lists — never a raw table dump."""
    from .opportunities import list_opportunities

    now = datetime.now(UTC)
    since_24h = (now - timedelta(hours=24)).isoformat()

    all_items = list_opportunities(storage, limit=500)
    with_price = [o for o in all_items if o["market_yes_probability"] is not None]
    sufficient_quality = [o for o in with_price if o["recommendation"] != "INSUFFICIENT_DATA"]
    watchlist_count = storage.connection.execute("SELECT COUNT(*) FROM watchlist_items").fetchone()[0]
    last_scan = storage.connection.execute(
        "SELECT MAX(started_at) FROM scanner_runs"
    ).fetchone()[0]

    top_opportunities = sorted(with_price, key=lambda o: -o["opportunity_score"])[:5]
    deadline_soon = sorted(
        [o for o in with_price if o["deadline_hours"] is not None and 0 <= o["deadline_hours"] <= 168],
        key=lambda o: o["deadline_hours"],
    )[:8]
    biggest_movers = sorted(
        [o for o in with_price if o["change_since_last_analysis"]],
        key=lambda o: abs(
            (o["change_since_last_analysis"]["market_yes_probability"]["to"] or 0)
            - (o["change_since_last_analysis"]["market_yes_probability"]["from"] or 0)
        ),
        reverse=True,
    )[:5]
    highest_liquidity = sorted(with_price, key=lambda o: -(o["liquidity"] or 0))[:5]
    biggest_deviation = sorted(with_price, key=lambda o: -abs(o["net_yes_edge"] or 0))[:5]
    new_markets = sorted(
        [o for o in all_items if o["first_seen_at"] and o["first_seen_at"] >= since_24h],
        key=lambda o: o["first_seen_at"], reverse=True,
    )[:5]
    data_problems = [o for o in all_items if o["status"] in ("Preis fehlt", "Datenlage unzureichend")][:8]

    recent_ai_runs = storage.connection.execute(
        "SELECT market_id, model, final_status, created_at FROM ai_analysis_runs "
        "WHERE analysis_type = 'explain_recommendation' ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    return {
        "generiert_am": now.isoformat(),
        "letzter_scan": last_scan,
        "uebersicht": {
            "aktive_maerkte": len(all_items),
            "maerkte_mit_preis": len(with_price),
            "maerkte_mit_ausreichender_datenqualitaet": len(sufficient_quality),
            "watchlist_anzahl": watchlist_count,
        },
        "interessanteste_maerkte": top_opportunities,
        "kurz_vor_entscheidung": deadline_soon,
        "groesste_preisbewegungen": biggest_movers,
        "hoechste_liquiditaet": highest_liquidity,
        "groesste_modellabweichung": biggest_deviation,
        "neue_maerkte": new_markets,
        "maerkte_mit_datenproblemen": data_problems,
        "letzte_ki_auswertungen": [
            {"market_id": r[0], "model": r[1], "status": r[2], "created_at": r[3]} for r in recent_ai_runs
        ],
    }


@app.post("/scan")
def trigger_scan(provider: str | None = None, limit: int | None = None, storage: Storage = Depends(get_storage)) -> dict:
    """Runs a real, on-demand market scan from the dashboard — the same
    logic the CLI's `scan` command uses (`cli._scan_one_provider`), so the
    normal user never needs PowerShell for day-to-day use. No auto-polling
    is started by this endpoint; it runs once per call."""
    from . import cli as cli_module
    from .providers.base import ProviderError

    settings = Settings.load()
    provider_names = [provider] if provider else cli_module.list_provider_names()
    results = []
    for provider_name in provider_names:
        try:
            read, saved, failed, _top_signals, new_shadow = cli_module._scan_one_provider(
                provider_name, settings, storage, limit or settings.scan_limit
            )
            results.append({
                "provider": provider_name, "markets_read": read, "snapshots_saved": saved,
                "markets_failed": failed, "new_shadow_setups": new_shadow, "error": None,
            })
        except ProviderError as exc:
            results.append({
                "provider": provider_name, "markets_read": 0, "snapshots_saved": 0,
                "markets_failed": 0, "new_shadow_setups": 0, "error": str(exc),
            })
    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "providers": results,
        "total_markets_read": sum(r["markets_read"] for r in results),
        "total_snapshots_saved": sum(r["snapshots_saved"] for r in results),
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Internal error", "type": type(exc).__name__})


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="dashboard")
