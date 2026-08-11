"""Block E, Part 3: Change Attribution.

Architectural decision (documented per the task, not left implicit): this
is DERIVED at read time by comparing two consecutive rows of the existing
`prediction_snapshots` table, NOT a new persisted table. Rationale:

  - `prediction_snapshots` is already the real, point-in-time-safe forecast
    history this project has built and tested (see ai/service.py's
    `_persist_prediction_snapshot`, called on every `get_prediction()` /
    `explain_recommendation()` run). Block E Part 4 (migration 22) already
    extended it with `model_hypothesis_probability`/`evidence_backed_
    probability`/`published_forecast_probability`/`forecast_maturity` —
    exactly the fields a change-attribution record needs.
  - A change attribution is fully reconstructable from two ordered rows of
    that table plus (best-effort, honestly optional) a correlated `events`
    row — no new fact needs to be written that doesn't already exist
    somewhere real.
  - A dedicated `change_attribution` table would duplicate storage for a
    value that is a pure function of two already-persisted rows, and would
    need its own backfill/consistency-with-snapshots logic for no real
    benefit. If a future round needs FAST lookup (e.g. "show me every
    change event across all markets without re-deriving"), that is the
    right trigger to add a persisted table — not this round.

`triggering_claim`/`source`/`factor`/`reason` are populated ONLY when a
real `events` row exists for the same market (provider/provider_market_id)
with `occurred_at`/`created_at` between the two snapshot timestamps —
never a fabricated correlation. Most changes will honestly have none.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage import Storage

# Which forecast field to attribute changes on: published_forecast_probability
# when a snapshot has one, else evidence_backed_probability (per the task's
# exact spec: "published_forecast_probability (or evidence_backed_probability
# if published is None)").
_FORECAST_COLUMNS = ("published_forecast_probability", "evidence_backed_probability")


@dataclass(frozen=True)
class ChangeAttribution:
    market_id: str
    previous_forecast: float | None
    new_forecast: float | None
    delta: float | None
    timestamp: str
    previous_timestamp: str
    field_used: str  # "published_forecast_probability" or "evidence_backed_probability"
    triggering_claim: str | None = None
    source: str | None = None
    factor: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "previous_forecast": self.previous_forecast,
            "new_forecast": self.new_forecast,
            "delta": self.delta,
            "timestamp": self.timestamp,
            "previous_timestamp": self.previous_timestamp,
            "field_used": self.field_used,
            "triggering_claim": self.triggering_claim,
            "source": self.source,
            "factor": self.factor,
            "reason": self.reason,
        }


def _effective_forecast(row: dict) -> tuple[float | None, str]:
    """Returns (value, which_field_was_used) per the task's exact fallback
    spec: published_forecast_probability, else evidence_backed_probability."""
    for col in _FORECAST_COLUMNS:
        val = row.get(col)
        if val is not None:
            return val, col
    return None, _FORECAST_COLUMNS[0]


def _find_triggering_event(storage: Storage, provider: str, provider_market_id: str, since: str, until: str) -> dict | None:
    """Best-effort real correlation: the most recent `events` row for this
    exact market with occurred_at (falling back to created_at) inside the
    (since, until] window between the two snapshots. None when no such row
    exists — never guessed, never the nearest-by-any-margin row outside the
    window."""
    row = storage.connection.execute(
        """
        SELECT title, source, source_url, occurred_at, created_at, event_type
        FROM events
        WHERE provider = ? AND provider_market_id = ?
          AND COALESCE(occurred_at, created_at) > ?
          AND COALESCE(occurred_at, created_at) <= ?
        ORDER BY COALESCE(occurred_at, created_at) DESC
        LIMIT 1
        """,
        (provider, provider_market_id, since, until),
    ).fetchone()
    if row is None:
        return None
    title, source, source_url, occurred_at, created_at, event_type = row
    return {
        "title": title, "source": source, "source_url": source_url,
        "occurred_at": occurred_at, "created_at": created_at, "event_type": event_type,
    }


def compute_change_attributions(storage: Storage, market_id: str, limit: int = 20) -> list[ChangeAttribution]:
    """Walks the market's prediction_snapshots history newest-first and
    returns one ChangeAttribution per consecutive pair where the effective
    forecast value actually changed (including None <-> a real number,
    which is itself a real, reportable change — a market moving from "no
    publishable forecast" to "publishable" or vice versa). Unchanged
    consecutive pairs are skipped entirely (not reported as zero-delta
    noise)."""
    rows = storage.connection.execute(
        """
        SELECT created_at, provider, provider_market_id,
               published_forecast_probability, evidence_backed_probability
        FROM prediction_snapshots
        WHERE market_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (market_id, limit + 1),
    ).fetchall()
    if len(rows) < 2:
        return []

    cols = ("created_at", "provider", "provider_market_id", "published_forecast_probability", "evidence_backed_probability")
    dict_rows = [dict(zip(cols, r, strict=True)) for r in rows]

    attributions: list[ChangeAttribution] = []
    for newer, older in pairwise(dict_rows):
        new_val, new_field = _effective_forecast(newer)
        old_val, _ = _effective_forecast(older)
        if new_val == old_val:
            continue
        delta = (new_val - old_val) if (new_val is not None and old_val is not None) else None

        event = _find_triggering_event(
            storage, newer["provider"], newer["provider_market_id"],
            since=older["created_at"], until=newer["created_at"],
        )
        triggering_claim = source = factor = reason = None
        if event is not None:
            triggering_claim = event["title"]
            source = event["source"] or event["source_url"]
            factor = event["event_type"]
            direction = "+" if (delta or 0) >= 0 else ""
            delta_pct = f"{direction}{delta:.1%}" if delta is not None else "n/a"
            reason = (
                f"{(old_val if old_val is not None else 0):.1%} -> {(new_val if new_val is not None else 0):.1%} "
                f"({delta_pct}), Trigger: {triggering_claim}, Quelle: {source or 'unbekannt'}"
            )

        attributions.append(ChangeAttribution(
            market_id=market_id, previous_forecast=old_val, new_forecast=new_val, delta=delta,
            timestamp=newer["created_at"], previous_timestamp=older["created_at"], field_used=new_field,
            triggering_claim=triggering_claim, source=source, factor=factor, reason=reason,
        ))
    return attributions
