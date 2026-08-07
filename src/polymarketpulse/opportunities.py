"""Turns the Prediction Engine V2's raw numbers into a ranked, understandable
"what's interesting right now" list — the product-facing layer the dashboard,
markets, and Chancen pages all read from. No new probability math happens
here; this module only labels and ranks what `prediction.compute_prediction`
already computed."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .ai import service as ai_service
from .ai.client import AIContextError
from .storage import Storage

STATUS_PRICE_MISSING = "Preis fehlt"
STATUS_INSUFFICIENT_DATA = "Datenlage unzureichend"
STATUS_DEADLINE_SOON = "Kurz vor Deadline"
STATUS_INTERESTING = "Interessant"
STATUS_WATCH = "Beobachten"
STATUS_NO_EDGE = "Keine klare Edge"
STATUS_RESOLUTION_UNCLEAR = "Resolution unklar"
STATUS_MANIPULATION_RISK = "Manipulationsrisiko hoch"

DEADLINE_URGENT_HOURS = 24


def _deadline_hours(end_date_iso: str | None, now: datetime) -> float | None:
    if not end_date_iso:
        return None
    try:
        end = datetime.fromisoformat(end_date_iso)
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - now).total_seconds() / 3600


def deadline_bucket(hours: float | None) -> str:
    if hours is None:
        return "unbekannt"
    if hours < 0:
        return "abgelaufen"
    if hours < 24:
        return "<24h"
    if hours < 72:
        return "1-3 Tage"
    if hours < 168:
        return "3-7 Tage"
    return ">7 Tage"


def _status_for(market_yes_price: float | None, prediction) -> str:
    if market_yes_price is None:
        return STATUS_PRICE_MISSING
    if prediction.recommendation == "INSUFFICIENT_DATA":
        return STATUS_INSUFFICIENT_DATA
    net_edge = prediction.net_yes_edge
    if net_edge is None or abs(net_edge) < 0.03:
        return STATUS_NO_EDGE
    manipulation_risk = prediction.manipulation_risk
    if manipulation_risk is not None and manipulation_risk.risk_score >= 60:
        return STATUS_MANIPULATION_RISK
    resolution_edge = prediction.resolution_edge
    if resolution_edge is not None and resolution_edge.risk_level == "hoch":
        return STATUS_RESOLUTION_UNCLEAR
    if prediction.confidence_score < 40:
        return STATUS_WATCH
    if abs(net_edge) >= 0.08 and prediction.confidence_score >= 55:
        return STATUS_INTERESTING
    return STATUS_WATCH


def _opportunity_score(prediction, liquidity: float | None, spread: float | None, deadline_hours: float | None) -> float:
    """Composite ranking score (0-100) — deliberately not just |edge|. A
    market with a big edge but low confidence must not outrank a market
    with a smaller, well-supported edge (explicit product requirement).
    Resolution clarity and cross-market inconsistency additionally pull the
    score down when the wording is a trap or related markets disagree in a
    way that isn't explained by fees/spread/differing rules."""
    if prediction.net_yes_edge is None:
        return 0.0
    edge_component = min(40.0, abs(prediction.net_yes_edge) * 100 * 2.2)  # ~18pp edge -> ~40 pts
    confidence_component = prediction.confidence_score * 0.30
    quality_component = prediction.data_quality.total * 0.15
    liquidity_component = min(10.0, ((liquidity or 0) / 100_000) * 10)
    spread_penalty = min(5.0, (spread or 0) * 100)
    deadline_component = 0.0
    if deadline_hours is not None and 0 <= deadline_hours < 168:
        deadline_component = max(0.0, 5.0 * (1 - deadline_hours / 168))

    resolution_edge = prediction.resolution_edge
    resolution_penalty = 0.0
    if resolution_edge is not None:
        # A low resolution_edge_score (unclear wording, no named authority,
        # no explicit deadline) directly reduces how "interesting" a market
        # can be, regardless of how big the raw edge looks.
        resolution_penalty = max(0.0, (60.0 - resolution_edge.resolution_edge_score) * 0.15)

    cross_market = prediction.cross_market
    inconsistency_penalty = 0.0
    if cross_market is not None and cross_market.logical_inconsistency_score is not None:
        inconsistency_penalty = min(10.0, cross_market.logical_inconsistency_score * 0.1)

    manipulation_risk = prediction.manipulation_risk
    manipulation_penalty = 0.0
    if manipulation_risk is not None:
        manipulation_penalty = min(15.0, manipulation_risk.risk_score * 0.15)

    reliability = prediction.market_reliability
    reliability_component = 0.0
    if reliability is not None and reliability.score is not None:
        reliability_component = (reliability.score - 50.0) * 0.1  # can be negative

    score = (
        edge_component + confidence_component + quality_component + liquidity_component + deadline_component
        + reliability_component
        - spread_penalty - resolution_penalty - inconsistency_penalty - manipulation_penalty
    )
    return round(max(0.0, min(100.0, score)), 1)


def _change_since_last(conn: sqlite3.Connection, market_id: str) -> dict | None:
    rows = conn.execute(
        "SELECT created_at, market_yes_probability, estimated_yes_probability, net_yes_edge, confidence_score "
        "FROM prediction_snapshots WHERE market_id = ? ORDER BY created_at DESC LIMIT 2",
        (market_id,),
    ).fetchall()
    if len(rows) < 2:
        return None
    current, previous = rows[0], rows[1]
    return {
        "previous_analysis_at": previous[0],
        "market_yes_probability": {"from": previous[1], "to": current[1]},
        "estimated_yes_probability": {"from": previous[2], "to": current[2]},
        "net_yes_edge": {"from": previous[3], "to": current[3]},
        "confidence_score": {"from": previous[4], "to": current[4]},
    }


def compute_opportunity(storage: Storage, market_row: dict) -> dict | None:
    """One market's opportunity view. Returns None only if the market
    cannot be loaded at all (e.g. deleted mid-request) — every other case
    (missing price, insufficient data) still returns a labeled entry
    rather than silently disappearing from the list."""
    try:
        prediction = ai_service.get_prediction(storage, market_row["market_id"])
    except AIContextError:
        return None

    now = datetime.now(UTC)
    hours_left = _deadline_hours(market_row["end_date"], now)
    status = _status_for(prediction.market_yes_probability, prediction)
    if status not in (STATUS_PRICE_MISSING, STATUS_INSUFFICIENT_DATA) and hours_left is not None and 0 <= hours_left < DEADLINE_URGENT_HOURS:
        status = STATUS_DEADLINE_SOON

    return {
        "market_id": market_row["market_id"],
        "provider": market_row["provider"],
        "provider_market_id": market_row["provider_market_id"],
        "question": market_row["question"],
        "category": market_row["category"],
        "url": market_row["url"],
        "market_yes_probability": prediction.market_yes_probability,
        "estimated_yes_probability": prediction.estimated_yes_probability,
        "net_yes_edge": prediction.net_yes_edge,
        "confidence_score": prediction.confidence_score,
        "data_quality_score": prediction.data_quality.total,
        "recommendation": prediction.recommendation,
        "status": status,
        "opportunity_score": _opportunity_score(prediction, market_row.get("liquidity"), market_row.get("spread"), hours_left),
        "liquidity": market_row.get("liquidity"),
        "volume_24h": market_row.get("volume_24h"),
        "deadline_hours": hours_left,
        "deadline_bucket": deadline_bucket(hours_left),
        "last_seen_at": market_row.get("last_seen_at"),
        "first_seen_at": market_row.get("first_seen_at"),
        "change_since_last_analysis": _change_since_last(storage.connection, market_row["market_id"]),
        "independent_evidence": prediction.independent_evidence.as_dict() if prediction.independent_evidence else None,
        "resolution_edge": prediction.resolution_edge.as_dict() if prediction.resolution_edge else None,
        "cross_market": prediction.cross_market.as_dict() if prediction.cross_market else None,
        "reaction_lag": prediction.reaction_lag.as_dict() if prediction.reaction_lag else None,
        "orderbook_metrics": prediction.orderbook_metrics.as_dict() if prediction.orderbook_metrics else None,
        "trade_flow_metrics": prediction.trade_flow_metrics.as_dict() if prediction.trade_flow_metrics else None,
        "wallet_concentration": prediction.wallet_concentration.as_dict() if prediction.wallet_concentration else None,
        "market_reliability": prediction.market_reliability.as_dict() if prediction.market_reliability else None,
        "manipulation_risk": prediction.manipulation_risk.as_dict() if prediction.manipulation_risk else None,
    }


def list_opportunities(storage: Storage, limit: int = 300) -> list[dict]:
    rows = storage.connection.execute(
        """
        SELECT m.market_id, m.provider, m.provider_market_id, m.question, m.category, m.url,
               m.end_date, m.first_seen_at, m.last_seen_at,
               s.yes_price, s.liquidity, s.volume_24h, s.spread
        FROM markets m
        LEFT JOIN (
            SELECT market_id, yes_price, liquidity, volume_24h, spread, captured_at,
                   ROW_NUMBER() OVER (PARTITION BY market_id ORDER BY captured_at DESC) AS rn
            FROM market_snapshots
        ) s ON s.market_id = m.market_id AND s.rn = 1
        WHERE m.resolution_status = 'unresolved'
        ORDER BY m.last_seen_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    cols = (
        "market_id", "provider", "provider_market_id", "question", "category", "url",
        "end_date", "first_seen_at", "last_seen_at", "yes_price", "liquidity", "volume_24h", "spread",
    )
    opportunities = []
    for r in rows:
        market_row = dict(zip(cols, r, strict=True))
        opp = compute_opportunity(storage, market_row)
        if opp is not None:
            opportunities.append(opp)
    return opportunities
