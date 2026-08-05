"""Deadline Engine — the time remaining until resolution is a first-class
input, not an afterthought. As a market gets closer to resolving, fresh
news and short-term price action deserve more weight than the slow-moving
historical base rate; far from resolution, the opposite holds. Every
threshold and weight here is a plain, documented constant — configurable,
not learned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

DEADLINE_PHASES: tuple[tuple[str, float | None], ...] = (
    ("MORE_THAN_7_DAYS", 7 * 24),
    ("SEVEN_DAYS", 3 * 24),
    ("SEVENTY_TWO_HOURS", 24),
    ("TWENTY_FOUR_HOURS", 6),
    ("SIX_HOURS", 1),
    ("ONE_HOUR", 1 / 60),
    ("FINAL_MINUTES", 0.0),
)

PHASE_LABEL_DE = {
    "MORE_THAN_7_DAYS": "mehr als 7 Tage",
    "SEVEN_DAYS": "7 Tage",
    "SEVENTY_TWO_HOURS": "72 Stunden",
    "TWENTY_FOUR_HOURS": "24 Stunden",
    "SIX_HOURS": "6 Stunden",
    "ONE_HOUR": "1 Stunde",
    "FINAL_MINUTES": "letzte Minuten",
    "UNKNOWN": "unbekannt",
    "RESOLVED_OR_PAST": "Auflösungszeitpunkt erreicht/überschritten",
}


@dataclass(frozen=True)
class DeadlineWeights:
    """Multiplicative weights applied by the ensemble/Bayesian update.
    `news_weight` scales how much fresh news evidence shifts the prior;
    `momentum_weight` scales the market/momentum submodel's ensemble
    weight; `history_weight` scales the (slow-moving) historical base rate
    submodel — inversely, so it never dominates in the closing minutes;
    `recommended_scan_interval_seconds` documents (not enforces — the
    scanner's own cadence config decides that) how often re-scanning this
    market is worthwhile at this phase."""

    phase: str
    news_weight: float
    momentum_weight: float
    history_weight: float
    recommended_scan_interval_seconds: int

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "phase_label": PHASE_LABEL_DE.get(self.phase, self.phase),
            "news_weight": self.news_weight,
            "momentum_weight": self.momentum_weight,
            "history_weight": self.history_weight,
            "recommended_scan_interval_seconds": self.recommended_scan_interval_seconds,
        }


# phase -> (news_weight, momentum_weight, history_weight, scan_interval_s)
_PHASE_WEIGHTS: dict[str, tuple[float, float, float, int]] = {
    "MORE_THAN_7_DAYS": (0.5, 0.6, 1.4, 6 * 3600),
    "SEVEN_DAYS": (0.7, 0.8, 1.2, 3600),
    "SEVENTY_TWO_HOURS": (1.0, 1.0, 1.0, 1800),
    "TWENTY_FOUR_HOURS": (1.3, 1.2, 0.8, 600),
    "SIX_HOURS": (1.6, 1.5, 0.6, 180),
    "ONE_HOUR": (2.0, 1.8, 0.4, 60),
    "FINAL_MINUTES": (2.5, 2.2, 0.25, 15),
}


def classify_deadline_phase(now: datetime, resolution_date: datetime | None) -> str:
    """Pure function: no I/O, easy to unit test exhaustively."""
    if resolution_date is None:
        return "UNKNOWN"
    if resolution_date.tzinfo is None:
        resolution_date = resolution_date.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    hours_remaining = (resolution_date - now).total_seconds() / 3600
    if hours_remaining <= 0:
        return "RESOLVED_OR_PAST"
    if hours_remaining > 7 * 24:
        return "MORE_THAN_7_DAYS"
    if hours_remaining > 3 * 24:
        return "SEVEN_DAYS"
    if hours_remaining > 24:
        return "SEVENTY_TWO_HOURS"
    if hours_remaining > 6:
        return "TWENTY_FOUR_HOURS"
    if hours_remaining > 1:
        return "SIX_HOURS"
    if hours_remaining > 1 / 60:
        return "ONE_HOUR"
    return "FINAL_MINUTES"


def deadline_weights_for(phase: str) -> DeadlineWeights:
    news_w, momentum_w, history_w, scan_s = _PHASE_WEIGHTS.get(phase, (1.0, 1.0, 1.0, 1800))
    return DeadlineWeights(
        phase=phase, news_weight=news_w, momentum_weight=momentum_w,
        history_weight=history_w, recommended_scan_interval_seconds=scan_s,
    )


def compute_deadline_weights(now: datetime, resolution_date: datetime | None) -> DeadlineWeights:
    phase = classify_deadline_phase(now, resolution_date)
    return deadline_weights_for(phase)
