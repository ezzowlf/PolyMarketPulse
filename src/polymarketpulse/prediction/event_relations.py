"""Event-Relations submodel — folds stored causal/correlational
event→market relations (see `events.py` and the `event_relations` table)
into the ensemble, but ONLY the ones with a quantitative evidence tier
(KNOWN / STRONG_EVIDENCE / SUPPORTED). PLAUSIBLE/SPECULATIVE/UNKNOWN
relations are still returned for display ("indirektes Signal, Einfluss:
gering") but contribute exactly zero to the probability — enforced via
`events.quantitative_weight_for_tier`, not a convention this module could
accidentally violate.

Like momentum.py, this submodel *adjusts* the market price rather than
replacing it — and, like momentum.py, it must report itself unavailable
(not "available with adjustment 0") when there is nothing quantitative to
contribute, so it never becomes a disguised market-price copy either.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..events import QUANTITATIVE_TIERS
from .types import SubmodelEstimate

MAX_EVENT_RELATION_ADJUSTMENT = 0.05


@dataclass(frozen=True)
class RelationSignal:
    relation_type: str
    direction: str
    evidence_tier: str
    strength: float | None
    confidence: float | None
    quantitative: bool
    detail: str

    def as_dict(self) -> dict:
        return {
            "relation_type": self.relation_type, "direction": self.direction, "evidence_tier": self.evidence_tier,
            "strength": self.strength, "confidence": self.confidence, "quantitative": self.quantitative,
            "detail": self.detail,
        }


def collect_event_relation_signals(conn: sqlite3.Connection, provider: str, provider_market_id: str) -> list[RelationSignal]:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "event_relations" not in tables:
        return []
    rows = conn.execute(
        "SELECT relation_type, direction, evidence_tier, strength, confidence, detail FROM event_relations "
        "WHERE target_provider = ? AND target_provider_market_id = ?",
        (provider, provider_market_id),
    ).fetchall()
    signals = []
    for relation_type, direction, evidence_tier, strength, confidence, detail in rows:
        quantitative = evidence_tier in QUANTITATIVE_TIERS and strength is not None and confidence is not None
        signals.append(RelationSignal(relation_type, direction, evidence_tier, strength, confidence, quantitative, detail))
    return signals


def compute_event_relation_estimate(
    signals: list[RelationSignal], market_yes_price: float | None
) -> SubmodelEstimate:
    if not signals:
        return SubmodelEstimate(
            name="event_relations", estimated_yes_probability=None, weight=0.0, available=False,
            detail="Keine gespeicherten Event-Beziehungen für diesen Markt.",
        )

    quantitative = [s for s in signals if s.quantitative]
    if not quantitative or market_yes_price is None:
        n_plausible = len(signals) - len(quantitative)
        return SubmodelEstimate(
            name="event_relations", estimated_yes_probability=None, weight=0.0, available=False,
            detail=(
                f"{len(signals)} Event-Beziehung(en) gefunden, aber {n_plausible} davon nur PLAUSIBLE/SPECULATIVE/"
                "UNKNOWN (keine quantitative Gewichtung) oder kein Marktpreis vorhanden — indirekte Signale werden "
                "nur zur Erklärung angezeigt, nicht in die Prognose eingerechnet."
            ),
        )

    raw_adjustment = sum(
        (1.0 if s.direction == "positive" else -1.0) * s.strength * s.confidence for s in quantitative
    )
    adjustment = max(-MAX_EVENT_RELATION_ADJUSTMENT, min(MAX_EVENT_RELATION_ADJUSTMENT, raw_adjustment))
    estimate = round(max(0.0, min(1.0, market_yes_price + adjustment)), 4)
    weight = min(0.2, 0.05 * len(quantitative))

    return SubmodelEstimate(
        name="event_relations", estimated_yes_probability=estimate, weight=weight, available=True,
        detail=(
            f"{len(quantitative)} belegte Event-Beziehung(en) (KNOWN/STRONG_EVIDENCE/SUPPORTED) -> "
            f"Anpassung {adjustment:+.1%} (gedeckelt auf ±{MAX_EVENT_RELATION_ADJUSTMENT:.0%})."
        ),
    )
