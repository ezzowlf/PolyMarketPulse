"""Market Reaction Lag — measures how long it took (or is taking) the
market price to move after the first public evidence report, using only
the price snapshots already collected in `market_snapshots`. No new data
source; this is a derived signal over existing history.

Snapshot cadence is scan-driven, not a fixed clock, so this deliberately
does not promise minute-granular before/after prices (the spec's
"5/15/30/60 minutes" is aspirational for a service that snapshots on
demand) — it reports what the actual snapshot history can support: the
price just before the evidence, the latest price, and — if a snapshot
shows a move past REACTION_THRESHOLD — how long that took."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

REACTION_THRESHOLD = 0.03  # 3pp move counts as "the market reacted"

STATUS_NO_EVIDENCE_TIME = "unbekannt"
STATUS_REACTED = "Markt hat reagiert"
STATUS_NOT_YET_REACTED = "noch keine erkennbare Reaktion"


@dataclass(frozen=True)
class ReactionLagResult:
    price_before_evidence: float | None
    latest_price: float | None
    reaction_detected_at_hours: float | None  # hours after first evidence that a >=3pp move was first seen
    status: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "price_before_evidence": self.price_before_evidence,
            "latest_price": self.latest_price,
            "reaction_detected_at_hours": self.reaction_detected_at_hours,
            "status": self.status,
            "detail": self.detail,
        }


def compute_market_reaction_lag(
    conn: sqlite3.Connection, market_id: str, first_evidence_at: datetime | None, now: datetime | None = None
) -> ReactionLagResult:
    now = now or datetime.now(UTC)
    if first_evidence_at is None:
        return ReactionLagResult(
            price_before_evidence=None, latest_price=None, reaction_detected_at_hours=None,
            status=STATUS_NO_EVIDENCE_TIME, detail="Kein Zeitpunkt für die erste Evidenz bekannt.",
        )
    if first_evidence_at.tzinfo is None:
        first_evidence_at = first_evidence_at.replace(tzinfo=UTC)

    rows = conn.execute(
        "SELECT captured_at, yes_price FROM market_snapshots WHERE market_id = ? ORDER BY captured_at ASC",
        (market_id,),
    ).fetchall()
    if not rows:
        return ReactionLagResult(
            price_before_evidence=None, latest_price=None, reaction_detected_at_hours=None,
            status=STATUS_NO_EVIDENCE_TIME, detail="Keine Preishistorie vorhanden.",
        )

    price_before = None
    latest_price = rows[-1][1]
    reaction_hours = None
    for captured_at, yes_price in rows:
        try:
            ts = datetime.fromisoformat(captured_at)
        except (ValueError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts <= first_evidence_at:
            price_before = yes_price
            continue
        if price_before is not None and yes_price is not None and abs(yes_price - price_before) >= REACTION_THRESHOLD:
            reaction_hours = round((ts - first_evidence_at).total_seconds() / 3600, 2)
            break

    if reaction_hours is not None:
        status = STATUS_REACTED
        detail = f"Preisbewegung >= {REACTION_THRESHOLD:.0%} {reaction_hours:.1f}h nach Erstmeldung erkannt."
    else:
        status = STATUS_NOT_YET_REACTED
        detail = "Bisher keine Preisbewegung >= 3 Prozentpunkte seit der Erstmeldung in der Historie erkennbar."

    return ReactionLagResult(
        price_before_evidence=price_before, latest_price=latest_price,
        reaction_detected_at_hours=reaction_hours, status=status, detail=detail,
    )
