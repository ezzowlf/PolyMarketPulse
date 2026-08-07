"""Manipulation Risk — explicitly a *risk indicator*, never a finding of
wrongdoing. Every reason string is a neutral, descriptive observation
("dünnes Orderbuch bei großer Bewegung"), never an accusation against a
person or wallet. See CLAUDE.md-style project rule: detection, not
manipulation, and never "this is insider trading"."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ManipulationRiskResult:
    risk_score: float  # 0..100
    reasons: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0  # 0..1, how much data backed this score
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "risk_score": self.risk_score, "reasons": list(self.reasons),
            "confidence": self.confidence, "detail": self.detail,
        }


def compute_manipulation_risk(
    orderbook_thin: bool | None,
    large_trade_ratio: float | None,
    price_moved_without_evidence: bool,
    wallet_concentration_score: float | None,
    deadline_hours: float | None,
) -> ManipulationRiskResult:
    reasons: list[str] = []
    score = 0.0
    data_points = 0

    if orderbook_thin is not None:
        data_points += 1
        if orderbook_thin:
            score += 20.0
            reasons.append("dünnes Orderbuch")

    if large_trade_ratio is not None:
        data_points += 1
        if large_trade_ratio >= 0.6:
            score += 20.0
            reasons.append("Markt wird von wenigen großen Trades bewegt")
        elif large_trade_ratio >= 0.4:
            score += 10.0

    if price_moved_without_evidence:
        data_points += 1
        score += 25.0
        reasons.append("Preisbewegung ohne bestätigte öffentliche Evidenz")

    if wallet_concentration_score is not None:
        data_points += 1
        if wallet_concentration_score >= 60:
            score += 20.0
            reasons.append("dominante öffentliche Adresse(n)")
        elif wallet_concentration_score >= 35:
            score += 8.0

    if deadline_hours is not None and 0 <= deadline_hours < 24:
        data_points += 1
        if orderbook_thin or (large_trade_ratio or 0) >= 0.4:
            score += 10.0
            reasons.append("auffällige Aktivität kurz vor Deadline")

    score = round(min(100.0, score), 1)
    confidence = round(min(1.0, data_points / 5.0), 2)

    detail = (
        f"Manipulation Risk Score {score}/100 (Confidence {confidence:.0%}), basierend auf {data_points} "
        "Datenpunkt(en). Hinweis: dies ist ein Risikoindikator, kein Nachweis für Fehlverhalten."
    )

    return ManipulationRiskResult(risk_score=score, reasons=tuple(reasons), confidence=confidence, detail=detail)
