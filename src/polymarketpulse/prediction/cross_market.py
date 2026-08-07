"""Cross-Market contradiction detector — finds other open markets whose
question shares enough subject terms with this one that they plausibly
describe the same or a logically dependent event, and flags cases where
their prices diverge more than a shared/dependent event should allow.

Deliberately conservative: this is a *research signal*, not a trading
arbitrage claim — fees, spread, liquidity, and differing resolution rules
between markets are real and are called out explicitly rather than papered
over. See `_detail` below.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .resolution_rules import _significant_terms

MIN_OVERLAP_CONFIDENCE = 0.35
STRONG_OVERLAP_CONFIDENCE = 0.6


@dataclass(frozen=True)
class RelatedMarket:
    market_id: str
    provider: str
    question: str
    yes_price: float | None
    overlap_confidence: float

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id, "provider": self.provider, "question": self.question,
            "yes_price": self.yes_price, "overlap_confidence": self.overlap_confidence,
        }


@dataclass(frozen=True)
class CrossMarketResult:
    available: bool
    related_markets: tuple[RelatedMarket, ...]
    max_divergence: float | None  # 0..1, biggest |price_a - price_b| among strongly related markets
    logical_inconsistency_score: float | None  # 0..100
    cross_provider_spread: float | None  # 0..1, max divergence among related markets on a different provider
    detail: str

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "related_markets": [m.as_dict() for m in self.related_markets],
            "max_divergence": self.max_divergence,
            "logical_inconsistency_score": self.logical_inconsistency_score,
            "cross_provider_spread": self.cross_provider_spread,
            "detail": self.detail,
        }


def _unavailable(detail: str) -> CrossMarketResult:
    return CrossMarketResult(
        available=False, related_markets=(), max_divergence=None,
        logical_inconsistency_score=None, cross_provider_spread=None, detail=detail,
    )


def compute_cross_market_relations(
    conn: sqlite3.Connection,
    market_id: str,
    provider: str,
    question: str,
    market_yes_price: float | None,
    limit: int = 200,
) -> CrossMarketResult:
    own_terms = set(_significant_terms(question, max_terms=20))
    if not own_terms:
        return _unavailable("Marktfrage liefert keine auswertbaren Begriffe für einen Cross-Market-Vergleich.")

    rows = conn.execute(
        """
        SELECT m.market_id, m.provider, m.question, s.yes_price
        FROM markets m
        LEFT JOIN (
            SELECT market_id, yes_price,
                   ROW_NUMBER() OVER (PARTITION BY market_id ORDER BY captured_at DESC) AS rn
            FROM market_snapshots
        ) s ON s.market_id = m.market_id AND s.rn = 1
        WHERE m.resolution_status = 'unresolved' AND m.market_id != ?
        LIMIT ?
        """,
        (market_id, limit),
    ).fetchall()

    related: list[RelatedMarket] = []
    for other_id, other_provider, other_question, other_price in rows:
        other_terms = set(_significant_terms(other_question or "", max_terms=20))
        if not other_terms:
            continue
        shared = own_terms & other_terms
        if not shared:
            continue
        confidence = len(shared) / min(len(own_terms), len(other_terms))
        if confidence < MIN_OVERLAP_CONFIDENCE:
            continue
        related.append(
            RelatedMarket(
                market_id=other_id, provider=other_provider, question=other_question,
                yes_price=other_price, overlap_confidence=round(confidence, 3),
            )
        )

    related.sort(key=lambda r: r.overlap_confidence, reverse=True)
    related = related[:10]

    if not related:
        return _unavailable("Keine logisch verwandten offenen Märkte mit ausreichender Begriffsüberlappung gefunden.")

    if market_yes_price is None:
        return CrossMarketResult(
            available=True, related_markets=tuple(related), max_divergence=None,
            logical_inconsistency_score=None, cross_provider_spread=None,
            detail=f"{len(related)} verwandte(r) Markt/Märkte gefunden, aber kein eigener Marktpreis für einen Divergenzvergleich vorhanden.",
        )

    strong = [r for r in related if r.overlap_confidence >= STRONG_OVERLAP_CONFIDENCE and r.yes_price is not None]
    max_divergence = None
    logical_inconsistency_score = None
    if strong:
        divergences = [abs(r.yes_price - market_yes_price) for r in strong]
        max_divergence = round(max(divergences), 4)
        # Weight by overlap confidence — a near-identical question with a
        # big price gap is a stronger inconsistency signal than a loosely
        # related one.
        weighted = max(
            abs(r.yes_price - market_yes_price) * r.overlap_confidence for r in strong
        )
        logical_inconsistency_score = round(min(100.0, weighted * 200), 1)

    cross_provider = [r for r in strong if r.provider != provider]
    cross_provider_spread = round(max((abs(r.yes_price - market_yes_price) for r in cross_provider), default=0.0), 4) if cross_provider else None

    detail = (
        f"{len(related)} verwandte(r) Markt/Märkte gefunden ({len(strong)} mit starker Begriffsüberlappung >= "
        f"{STRONG_OVERLAP_CONFIDENCE:.0%}). "
        + (f"Maximale Preisdivergenz {max_divergence:+.1%}. " if max_divergence is not None else "Kein Preisvergleich möglich. ")
        + "Hinweis: Gebühren, Spread, Liquidität und unterschiedliche Resolution-Regeln zwischen Märkten sind hier "
        "nicht berücksichtigt — dies ist ein Research-Signal, keine belastbare Arbitrage-Aussage."
    )

    return CrossMarketResult(
        available=True, related_markets=tuple(related), max_divergence=max_divergence,
        logical_inconsistency_score=logical_inconsistency_score, cross_provider_spread=cross_provider_spread,
        detail=detail,
    )
