"""Information shock and lead calculations from persisted, linked inputs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class InformationShock:
    level: str
    novelty: float
    authority: float
    independence: float
    resolution_relevance: float
    state_change_size: float


def assess_shock(*, novelty: float, authority: float, independence: float, resolution_relevance: float, state_change_size: float) -> InformationShock:
    """Deterministic classification. A repost remains LOW because novelty and
    independence are low; no probability is calculated here."""
    values = [novelty, authority, independence, resolution_relevance, state_change_size]
    if any(not 0 <= value <= 1 for value in values):
        raise ValueError("shock inputs must be probabilities")
    score = .25 * novelty + .25 * authority + .15 * independence + .2 * resolution_relevance + .15 * state_change_size
    level = "CRITICAL" if score >= .82 else "HIGH" if score >= .62 else "MEDIUM" if score >= .35 else "LOW"
    return InformationShock(level, novelty, authority, independence, resolution_relevance, state_change_size)


def lead_hours(start: str | None, end: str | None) -> float | None:
    """Only derive a lead with two parseable, ordered timestamps."""
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(delta.total_seconds() / 3600, 3) if delta.total_seconds() >= 0 else None
