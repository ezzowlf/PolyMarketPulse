"""Market Reliability Score — a single, auditable "how much should you
trust this market's current price" label, combining resolution clarity,
order-book depth/imbalance, wallet concentration, cross-market consistency,
and whether price moves have public evidence behind them. Never claims
manipulation is proven — see manipulation.py for the separate, explicitly
risk-only score.
"""

from __future__ import annotations

from dataclasses import dataclass

LEVEL_HIGH = "hoch"
LEVEL_MEDIUM = "mittel"
LEVEL_LOW = "niedrig"
LEVEL_INSUFFICIENT = "unzureichende Daten"


@dataclass(frozen=True)
class MarketReliabilityResult:
    level: str
    score: float | None  # 0..100, None if insufficient inputs
    components: dict
    detail: str

    def as_dict(self) -> dict:
        return {"level": self.level, "score": self.score, "components": self.components, "detail": self.detail}


def compute_market_reliability(
    resolution_edge_score: float | None,
    orderbook_imbalance: float | None,
    orderbook_thin: bool | None,
    wallet_concentration_score: float | None,
    cross_market_inconsistency_score: float | None,
    price_moved_without_evidence: bool,
) -> MarketReliabilityResult:
    inputs_present = sum(
        x is not None for x in (resolution_edge_score, orderbook_imbalance, wallet_concentration_score)
    )
    if inputs_present == 0:
        return MarketReliabilityResult(
            level=LEVEL_INSUFFICIENT, score=None, components={},
            detail="Zu wenige Eingangsdaten (Resolution-Klarheit, Orderbuch, Wallet-Konzentration) für eine Reliability-Bewertung.",
        )

    components: dict[str, float] = {}
    score = 60.0  # neutral baseline

    if resolution_edge_score is not None:
        components["resolution_edge"] = resolution_edge_score
        score += (resolution_edge_score - 50.0) * 0.25

    if orderbook_thin:
        components["orderbook_thin_penalty"] = -15.0
        score -= 15.0
    if orderbook_imbalance is not None:
        components["orderbook_imbalance"] = orderbook_imbalance
        score -= abs(orderbook_imbalance) * 10.0

    if wallet_concentration_score is not None:
        components["wallet_concentration"] = wallet_concentration_score
        score -= (wallet_concentration_score / 100.0) * 20.0

    if cross_market_inconsistency_score is not None:
        components["cross_market_inconsistency"] = cross_market_inconsistency_score
        score -= (cross_market_inconsistency_score / 100.0) * 10.0

    if price_moved_without_evidence:
        components["price_move_without_evidence_penalty"] = -10.0
        score -= 10.0

    score = round(max(0.0, min(100.0, score)), 1)
    if score >= 65:
        level = LEVEL_HIGH
    elif score >= 45:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    reasons = []
    if orderbook_thin:
        reasons.append("dünnes Orderbuch")
    if wallet_concentration_score is not None and wallet_concentration_score >= 50:
        reasons.append("hohe Wallet-Konzentration")
    if price_moved_without_evidence:
        reasons.append("Preisbewegung ohne bestätigte Evidenz")
    if resolution_edge_score is not None and resolution_edge_score < 40:
        reasons.append("unklare Resolution-Regeln")

    detail = f"Reliability-Score {score}/100." + (f" Gründe: {', '.join(reasons)}." if reasons else " Keine besonderen Risikofaktoren erkannt.")

    return MarketReliabilityResult(level=level, score=score, components=components, detail=detail)
