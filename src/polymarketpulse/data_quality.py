from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import Market

# Fields whose absence materially hurts research usefulness. Weighted
# equally for transparency — no hidden importance ranking.
_REQUIRED_FIELDS = (
    "yes_price",
    "liquidity",
    "volume_24h",
    "spread",
    "end_at",
    "category",
)


@dataclass(frozen=True)
class QualityReport:
    market_id: str
    score: float  # 0-100
    issues: tuple[str, ...]
    checks_passed: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "score": self.score,
            "issues": list(self.issues),
            "checks_passed": list(self.checks_passed),
        }


def assess_market(market: Market, now: datetime | None = None) -> QualityReport:
    """Score a single market's data quality. Deductions are itemized so the
    score is always explainable — never a black-box number."""
    issues: list[str] = []
    passed: list[str] = []
    penalty = 0.0

    missing_required = [f for f in _REQUIRED_FIELDS if getattr(market, f) in (None, "")]
    if missing_required:
        penalty += len(missing_required) * 8.0
        issues.append(f"fehlende Pflichtfelder: {', '.join(missing_required)}")
    else:
        passed.append("alle Pflichtfelder vorhanden")

    if market.missing_fields:
        penalty += min(20.0, len(market.missing_fields) * 3.0)
        issues.append(f"unvollständige Rohdaten ({len(market.missing_fields)} Felder fehlten im Provider-Payload)")
    else:
        passed.append("keine fehlenden Rohdatenfelder")

    if market.yes_price is not None and not (0.0 <= market.yes_price <= 1.0):
        penalty += 25.0
        issues.append(f"ungültiger YES-Preis außerhalb [0,1]: {market.yes_price}")
    if market.no_price is not None and not (0.0 <= market.no_price <= 1.0):
        penalty += 25.0
        issues.append(f"ungültiger NO-Preis außerhalb [0,1]: {market.no_price}")

    if market.yes_price is not None and market.no_price is not None:
        total = market.yes_price + market.no_price
        if abs(total - 1.0) > 0.05:
            penalty += 10.0
            issues.append(f"YES+NO weicht von 1.0 ab ({total:.3f})")
        else:
            passed.append("YES+NO konsistent (≈1.0)")

    if market.liquidity < 0:
        penalty += 30.0
        issues.append(f"negative Liquidität: {market.liquidity}")
    if market.volume_24h < 0:
        penalty += 30.0
        issues.append(f"negatives Volumen: {market.volume_24h}")
    if market.liquidity >= 0 and market.volume_24h >= 0:
        passed.append("keine negativen Volumen-/Liquiditätswerte")

    if market.spread is not None and (market.spread < 0 or market.spread > 1):
        penalty += 15.0
        issues.append(f"unplausibler Spread: {market.spread}")

    if market.end_at is not None and market.start_at is not None and market.end_at < market.start_at:
        penalty += 20.0
        issues.append("Enddatum liegt vor dem Startdatum")
    elif market.end_at is not None and market.start_at is not None:
        passed.append("Start-/Enddatum konsistent")

    if now is not None and market.end_at is not None and market.end_at < now and market.resolution_status.value == "unresolved":
        penalty += 10.0
        issues.append("Enddatum liegt in der Vergangenheit, aber Markt gilt noch als unresolved")

    score = round(max(0.0, min(100.0, 100.0 - penalty)), 1)
    return QualityReport(
        market_id=market.provider_market_id,
        score=score,
        issues=tuple(issues),
        checks_passed=tuple(passed),
    )


@dataclass(frozen=True)
class SnapshotConsistencyReport:
    duplicate_snapshots: int
    out_of_order_snapshots: int
    issues: tuple[str, ...] = field(default_factory=tuple)


def assess_snapshot_sequence(rows: list[tuple[str, float | None]]) -> SnapshotConsistencyReport:
    """Check a chronologically-sorted list of (captured_at, yes_price) rows
    for duplicate timestamps and out-of-order timestamps. Pure function over
    already-fetched rows — callers pull the rows from `market_snapshots`."""
    duplicates = 0
    out_of_order = 0
    issues: list[str] = []
    prev_ts: str | None = None
    seen_ts: set[str] = set()

    for captured_at, _yes_price in rows:
        if captured_at in seen_ts:
            duplicates += 1
            issues.append(f"doppelter Snapshot-Zeitstempel: {captured_at}")
        seen_ts.add(captured_at)
        if prev_ts is not None and captured_at < prev_ts:
            out_of_order += 1
            issues.append(f"Snapshot außerhalb der Reihenfolge: {captured_at} nach {prev_ts}")
        prev_ts = captured_at

    return SnapshotConsistencyReport(
        duplicate_snapshots=duplicates,
        out_of_order_snapshots=out_of_order,
        issues=tuple(issues),
    )
