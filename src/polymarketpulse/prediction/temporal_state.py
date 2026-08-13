"""Phase D — Temporal Intelligence: is a claim still current, or has it
gone stale / been disputed / been superseded?

The core distinction the project owner asked for: a STRUCTURAL fact (a
resolution-path step actually confirmed, e.g. "House passed the bill")
does not decay with time -- it stays true regardless of how long ago it
happened. A CONTEXTUAL/observational report (e.g. "shipping disruption
reported", a generic news claim) genuinely can go stale after hours or
days, because newer information may have superseded it without anyone
having explicitly said so.

This module is the single place that classification happens, so
evidence.py/world_state.py/the API never each invent their own staleness
rule.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

STATUS_CURRENT = "CURRENT"
STATUS_HISTORICAL = "HISTORICAL"
STATUS_EXPECTED = "EXPECTED"
STATUS_DISPUTED = "DISPUTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_UNKNOWN = "UNKNOWN"

ALL_STATUSES = (
    STATUS_CURRENT, STATUS_HISTORICAL, STATUS_EXPECTED, STATUS_DISPUTED,
    STATUS_EXPIRED, STATUS_SUPERSEDED, STATUS_UNKNOWN,
)

# Claim types that represent a structural fact about the resolution path
# itself (confirmed via claim_market_links -- see evidence.py) rather than
# a decaying observational report. Structural facts never go EXPIRED
# purely from age.
STRUCTURAL_CLAIM_TYPES = frozenset({"PATH_STEP", "DIRECT_RESOLUTION"})

# Freshness windows for non-structural claim types, in hours. A claim
# older than its window (and not structural, not superseded, not
# disputed) is honestly reported as EXPIRED rather than silently kept
# CURRENT forever. Deliberately conservative and explicit per type --
# no single global TTL, since a quantitative data point (e.g. IMF
# PortWatch transit averages) stays meaningful far longer than a single
# breaking-news report.
_DEFAULT_FRESHNESS_HOURS = {
    "CONTEXT": 72,
    "QUANTITATIVE_SIGNAL": 24 * 7,
}
_FALLBACK_FRESHNESS_HOURS = 72


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def compute_temporal_status(
    *,
    claim_type: str | None,
    timestamp: str | None,
    now: datetime,
    superseded_by: str | None = None,
    has_counter_evidence: bool = False,
    expected_at: str | None = None,
    valid_until: str | None = None,
) -> str:
    """Pure classification -- no DB access. Precedence, most authoritative
    first: an explicit successor claim always wins (SUPERSEDED); an
    unresolved contradiction is DISPUTED even for an otherwise-fresh
    structural fact (the honest state is "we don't know which is right",
    not "the older one is still current"); a future-dated expectation is
    EXPECTED; a real, parseable validity window (valid_until, e.g. from
    event_relations) is authoritative over the generic per-type freshness
    heuristic; structural facts without any of the above are CURRENT
    forever; everything else falls back to the per-claim-type freshness
    window."""
    if superseded_by:
        return STATUS_SUPERSEDED
    if has_counter_evidence:
        return STATUS_DISPUTED

    occurred = _parse(timestamp)
    expected = _parse(expected_at)
    if occurred is None and expected is not None and expected > now:
        return STATUS_EXPECTED
    if occurred is None:
        return STATUS_UNKNOWN
    if occurred > now:
        return STATUS_EXPECTED

    until = _parse(valid_until)
    if until is not None:
        return STATUS_CURRENT if now <= until else STATUS_EXPIRED

    if claim_type in STRUCTURAL_CLAIM_TYPES:
        return STATUS_CURRENT

    freshness_hours = _DEFAULT_FRESHNESS_HOURS.get(claim_type or "", _FALLBACK_FRESHNESS_HOURS)
    age_hours = (now - occurred).total_seconds() / 3600.0
    return STATUS_CURRENT if age_hours <= freshness_hours else STATUS_EXPIRED


def get_claim_temporal_status(
    conn: sqlite3.Connection, claim_id: str, claim_type: str | None, timestamp: str | None,
    now: datetime, expected_at: str | None = None, valid_until: str | None = None,
) -> str:
    """Real DB-backed lookup: checks the claim's own `superseded_by`
    column and whether any row in `claim_counter_evidence` names this
    claim, then delegates to the pure classifier above."""
    superseded_by = None
    try:
        row = conn.execute("SELECT superseded_by FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        superseded_by = row[0] if row else None
    except sqlite3.Error:
        pass

    has_counter_evidence = False
    try:
        row = conn.execute(
            "SELECT 1 FROM claim_counter_evidence WHERE claim_id = ? LIMIT 1", (claim_id,)
        ).fetchone()
        has_counter_evidence = row is not None
    except sqlite3.Error:
        pass

    return compute_temporal_status(
        claim_type=claim_type, timestamp=timestamp, now=now,
        superseded_by=superseded_by, has_counter_evidence=has_counter_evidence,
        expected_at=expected_at, valid_until=valid_until,
    )
