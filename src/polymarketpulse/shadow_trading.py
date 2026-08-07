"""Shadow-Trading simulation layer — never places a real order, never
touches a wallet. A "shadow trade" is a fully simulated position: entry at
the current market price, tracked lifecycle, simulated fee/slippage, and
an eventual simulated P&L once the market resolves (or another exit rule
fires). This module decides whether a market qualifies for a new shadow
trade right now, and persists the decision either way — including *why
not* when it doesn't qualify, so the filter thresholds themselves can be
evaluated later (too strict? too loose?) instead of only ever seeing the
markets that passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .prediction.types import PredictionResult

STATUS_CANDIDATE = "candidate"
STATUS_SKIPPED = "skipped"

DIRECTION_YES = "YES"
DIRECTION_NO = "NO"
DIRECTION_NONE = "NONE"


@dataclass(frozen=True)
class ShadowThresholds:
    """Every qualification threshold in one place, so a config change is a
    one-line diff and every shadow trade can record exactly which config it
    was decided under (see engine_version/config_hash on the snapshot)."""

    min_edge: float = 0.05
    min_confidence: float = 50.0
    min_data_quality: float = 50.0
    min_reliability_score: float = 45.0
    min_resolution_clarity: float = 40.0
    min_opportunity_score: float = 55.0
    max_spread: float = 0.08
    min_liquidity: float = 5_000.0
    max_manipulation_risk: float = 55.0
    assumed_stake: float = 1.0
    simulated_fee_rate: float = 0.02
    simulated_slippage_rate: float = 0.005

    # Exit rules (F) — all configurable, all logged as `exit_reason`.
    exit_edge_floor: float = 0.01  # edge shrinks below this -> "Edge verschwunden"
    exit_confidence_floor: float = 35.0  # confidence falls below this -> "Confidence gefallen"
    exit_manipulation_ceiling: float = 75.0  # manipulation risk rises above this -> exit
    exit_max_holding_hours: float = 24 * 14  # time-based exit safety valve (14 days)


@dataclass(frozen=True)
class ShadowDecision:
    market_id: str
    provider: str
    provider_market_id: str
    direction: str
    status: str  # candidate | skipped
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    entry_market_price: float | None
    independent_probability: float | None
    expected_edge: float | None
    confidence: float | None
    opportunity_score: float | None
    reliability_score: float | None
    manipulation_risk: float | None
    deadline_phase: str | None
    assumed_stake: float
    simulated_fee: float
    simulated_slippage: float

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id, "provider": self.provider, "provider_market_id": self.provider_market_id,
            "direction": self.direction, "status": self.status, "reasons": list(self.reasons),
            "blockers": list(self.blockers), "entry_market_price": self.entry_market_price,
            "independent_probability": self.independent_probability, "expected_edge": self.expected_edge,
            "confidence": self.confidence, "opportunity_score": self.opportunity_score,
            "reliability_score": self.reliability_score, "manipulation_risk": self.manipulation_risk,
            "deadline_phase": self.deadline_phase, "assumed_stake": self.assumed_stake,
            "simulated_fee": self.simulated_fee, "simulated_slippage": self.simulated_slippage,
        }


def evaluate_shadow_qualification(
    market_id: str, provider: str, provider_market_id: str,
    prediction: PredictionResult, opportunity: dict | None, spread: float | None, liquidity: float | None,
    thresholds: ShadowThresholds | None = None,
) -> ShadowDecision:
    thresholds = thresholds or ShadowThresholds()
    reasons: list[str] = []
    blockers: list[str] = []

    ie = prediction.independent_evidence
    rel = prediction.market_reliability
    risk = prediction.manipulation_risk
    re = prediction.resolution_edge
    opp_score = opportunity["opportunity_score"] if opportunity else None

    if ie is None or not ie.available or ie.independent_yes_probability is None:
        blockers.append("keine unabhängige Wahrscheinlichkeit verfügbar")
    if prediction.market_yes_probability is None:
        blockers.append("kein Marktpreis vorhanden")
    if prediction.net_yes_edge is None or abs(prediction.net_yes_edge) < thresholds.min_edge:
        blockers.append(f"Edge unter Mindestschwelle ({thresholds.min_edge:.0%})")
    if prediction.confidence_score < thresholds.min_confidence:
        blockers.append(f"Confidence unter Mindestschwelle ({thresholds.min_confidence:.0f})")
    if prediction.data_quality.total < thresholds.min_data_quality:
        blockers.append(f"Datenqualität unter Mindestschwelle ({thresholds.min_data_quality:.0f})")
    if rel is None or rel.score is None or rel.score < thresholds.min_reliability_score:
        blockers.append(f"Market Reliability unter Mindestschwelle ({thresholds.min_reliability_score:.0f})")
    if re is None or re.resolution_edge_score < thresholds.min_resolution_clarity:
        blockers.append(f"Resolution-Klarheit unter Mindestschwelle ({thresholds.min_resolution_clarity:.0f})")
    if opp_score is None or opp_score < thresholds.min_opportunity_score:
        blockers.append(f"Opportunity Score unter Mindestschwelle ({thresholds.min_opportunity_score:.0f})")
    if spread is not None and spread > thresholds.max_spread:
        blockers.append(f"Spread über Maximum ({thresholds.max_spread:.0%})")
    if liquidity is not None and liquidity < thresholds.min_liquidity:
        blockers.append(f"Liquidität unter Minimum (${thresholds.min_liquidity:,.0f})")
    if risk is not None and risk.risk_score > thresholds.max_manipulation_risk:
        blockers.append(f"Manipulation Risk über Maximum ({thresholds.max_manipulation_risk:.0f})")
    if ie is not None and ie.available and ie.contradiction_detected:
        blockers.append("widersprüchliche Evidenz vorhanden")
    if prediction.recommendation == "INSUFFICIENT_DATA":
        blockers.append("Empfehlung ist INSUFFICIENT_DATA")

    direction = DIRECTION_NONE
    if not blockers and prediction.net_yes_edge is not None:
        direction = DIRECTION_YES if prediction.net_yes_edge > 0 else DIRECTION_NO
        reasons.append(f"Edge {prediction.net_yes_edge:+.1%} bei Confidence {prediction.confidence_score:.0f}")
        if ie is not None and ie.available:
            reasons.append(f"unabhängige Schätzung {ie.independent_yes_probability:.0%} mit {ie.confirmation_count} Bestätigung(en)")
        if rel is not None:
            reasons.append(f"Market Reliability {rel.level}")

    status = STATUS_CANDIDATE if not blockers else STATUS_SKIPPED
    stake = thresholds.assumed_stake
    fee = round(stake * thresholds.simulated_fee_rate, 4)
    slippage = round(stake * thresholds.simulated_slippage_rate, 4)

    return ShadowDecision(
        market_id=market_id, provider=provider, provider_market_id=provider_market_id,
        direction=direction, status=status, reasons=tuple(reasons), blockers=tuple(blockers),
        entry_market_price=prediction.market_yes_probability,
        independent_probability=ie.independent_yes_probability if ie and ie.available else None,
        expected_edge=prediction.net_yes_edge, confidence=prediction.confidence_score,
        opportunity_score=opp_score, reliability_score=rel.score if rel else None,
        manipulation_risk=risk.risk_score if risk else None, deadline_phase=prediction.deadline_phase,
        assumed_stake=stake, simulated_fee=fee, simulated_slippage=slippage,
    )


def _directional_move(direction: str, entry_price: float | None, current_price: float | None) -> float | None:
    """Positive = favorable move for the simulated position's direction."""
    if entry_price is None or current_price is None:
        return None
    return (current_price - entry_price) if direction == DIRECTION_YES else (entry_price - current_price)


@dataclass(frozen=True)
class LifecycleUpdate:
    fields: dict = field(default_factory=dict)  # columns to write via update_shadow_trade_lifecycle
    exit_reason: str | None = None  # None -> stays open


def compute_lifecycle_update(
    trade_row: dict, current_market_price: float | None, prediction: PredictionResult | None,
    deadline_hours: float | None, resolution_status: str | None, winning_outcome: str | None,
    now: datetime | None = None, thresholds: ShadowThresholds | None = None,
) -> LifecycleUpdate:
    """Pure function: given the current DB row for an open shadow trade plus
    fresh market/prediction state, returns the fields to persist and — if an
    exit rule fires — the exit reason. Never mutates the DB itself (caller
    persists via `Storage.update_shadow_trade_lifecycle` /
    `Storage.close_shadow_trade`), so this stays independently testable."""
    thresholds = thresholds or ShadowThresholds()
    now = now or datetime.now(UTC)
    direction = trade_row["direction"]
    entry_price = trade_row["entry_market_price"]

    fields: dict = {}
    move = _directional_move(direction, entry_price, current_market_price)
    if move is not None:
        prev_fav = trade_row.get("max_favorable_move") or 0.0
        prev_adv = trade_row.get("max_adverse_move") or 0.0
        prev_drawdown = trade_row.get("max_drawdown") or 0.0
        new_fav = max(prev_fav, move)
        new_adv = min(prev_adv, move)
        drawdown_now = max(0.0, new_fav - move)
        fields["max_favorable_move"] = round(new_fav, 4)
        fields["max_adverse_move"] = round(new_adv, 4)
        fields["max_drawdown"] = round(max(prev_drawdown, drawdown_now), 4)

    created_at = datetime.fromisoformat(trade_row["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    holding_hours = (now - created_at).total_seconds() / 3600

    for hours, column in ((5 / 60, "price_after_5m"), (0.25, "price_after_15m"), (1, "price_after_1h"),
                          (6, "price_after_6h"), (24, "price_after_24h")):
        if trade_row.get(column) is None and holding_hours >= hours and current_market_price is not None:
            fields[column] = current_market_price

    if resolution_status == "resolved":
        return LifecycleUpdate(fields=fields, exit_reason="Resolution")
    if resolution_status in ("cancelled", "invalid", "disputed"):
        # Neutral treatment — no outcome to score against, no simulated
        # P&L, never counted as a win or a loss.
        return LifecycleUpdate(fields=fields, exit_reason=f"Markt {resolution_status} (neutral, kein Ergebnis)")

    if deadline_hours is not None and deadline_hours <= 0:
        fields["price_at_deadline"] = current_market_price
        return LifecycleUpdate(fields=fields, exit_reason="Deadline erreicht")

    if holding_hours >= thresholds.exit_max_holding_hours:
        return LifecycleUpdate(fields=fields, exit_reason="Time-based Exit (Maximaldauer erreicht)")

    if prediction is not None:
        if prediction.net_yes_edge is not None and abs(prediction.net_yes_edge) < thresholds.exit_edge_floor:
            return LifecycleUpdate(fields=fields, exit_reason="Edge verschwunden")
        if prediction.confidence_score < thresholds.exit_confidence_floor:
            return LifecycleUpdate(fields=fields, exit_reason="Confidence unter Schwelle gefallen")
        if prediction.independent_evidence is not None and prediction.independent_evidence.available and prediction.independent_evidence.contradiction_detected:
            return LifecycleUpdate(fields=fields, exit_reason="neue widersprüchliche Evidenz")
        if prediction.manipulation_risk is not None and prediction.manipulation_risk.risk_score > thresholds.exit_manipulation_ceiling:
            return LifecycleUpdate(fields=fields, exit_reason="Manipulation Risk stark gestiegen")

    return LifecycleUpdate(fields=fields, exit_reason=None)


def compute_shadow_pnl(direction: str, entry_price: float, stake: float, fee: float, slippage: float, won: bool) -> tuple[float, float]:
    """Simulated P&L/ROI of one resolved shadow trade — same fixed-stake
    payoff model as evaluation.py's simulated ROI (buy 1 share of the
    predicted side at its implied price; payoff (1-price) if it wins,
    -price if it loses), with simulated fee/slippage subtracted. Returns
    (pnl, roi). Never a real trade."""
    price = entry_price if direction == DIRECTION_YES else (1 - entry_price)
    raw_pnl = (1 - price) * stake if won else -price * stake
    pnl = round(raw_pnl - fee - slippage, 4)
    roi = round(pnl / stake, 4) if stake > 0 else None
    return pnl, roi
