"""Quantitative Price-Threshold Model — forecast crypto price-threshold
markets using real underlying price data when available.

Supports questions such as:
  - BTC above X by date
  - BTC below X by date
  - ETH above/below X
  - Other supported assets where price data exists

Inputs (when available):
  - current underlying price (from free, keyless APIs)
  - threshold
  - direction (above/below)
  - time to deadline
  - historical realized volatility
  - historical returns

Critical constraints:
  - Do NOT use Polymarket price (market-blind)
  - Do NOT introduce paid data sources
  - Use a transparent probabilistic approach
  - If price or volatility data is unavailable: return unavailable honestly

Design principle:
  - Expose all assumptions clearly
  - Handle: threshold already crossed, far away, very short horizon,
    longer horizon, missing price, missing volatility, expired deadline"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .types import DataQualityBreakdown

# Event types this model handles
_EVENT_TYPES = frozenset({
    "price_above", "price_below",
})

# Supported assets ( CoinGecko IDs)
# Kept to assets with free, keyless API access
_SUPPORTED_ASSETS = frozenset({
    "bitcoin", "btc",
    "ethereum", "eth", "ether",
    "solana", "sol",
    "dogecoin", "doge",
    "xrp", "ripple",
    "cardano", "ada",
})

# Threshold multipliers for probabilistic estimation
# These are conservative heuristics, not precise models
_SHORT_HORIZON_DAYS = 7
_MEDIUM_HORIZON_DAYS = 30
_LONG_HORIZON_DAYS = 90


@dataclass(frozen=True)
class QuantResult:
    """Result of the quantitative price-threshold forecast model."""

    available: bool
    probability: float | None
    confidence: float
    data_quality: DataQualityBreakdown
    reason: str
    inputs_used: tuple[str, ...]
    contributions: tuple[dict, ...]
    uncertainty: float

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "probability": self.probability,
            "confidence": self.confidence,
            "data_quality": self.data_quality.as_dict(),
            "reason": self.reason,
            "inputs_used": list(self.inputs_used),
            "contributions": list(self.contributions),
            "uncertainty": self.uncertainty,
        }


def _estimate_price_probability(
    current_price: float,
    threshold: float,
    direction: str,
    time_to_deadline_days: float | None,
    historical_volatility: float | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Estimate probability using a simplified Brownian motion approximation.

    This is NOT a precise financial model — it's a transparent heuristic
    that exposes its assumptions and handles edge cases gracefully.

    Formula sketch:
      - z-score = (threshold - current) / (current * vol * sqrt(days/365))
      - For direction="above": P = 1 - CDF(z) if threshold > current
      - For direction="below": P = CDF(z) if threshold < current

    Simplified to a bounded probability based on z-score magnitude."""
    inputs_used: list[str] = []

    # Edge case: threshold already crossed
    if direction == "above" and current_price >= threshold:
        inputs_used.append("threshold_already_crossed_above")
        return 0.95, "Threshold already exceeded (current price >= threshold)", tuple(inputs_used)
    if direction == "below" and current_price <= threshold:
        inputs_used.append("threshold_already_crossed_below")
        return 0.05, "Threshold already below (current price <= threshold)", tuple(inputs_used)

    # Edge case: missing volatility data
    if historical_volatility is None or historical_volatility <= 0:
        inputs_used.append("missing_volatility")
        return None, "Insufficient volatility data for probabilistic estimation", tuple(inputs_used)

    # Edge case: missing time horizon
    if time_to_deadline_days is None or time_to_deadline_days <= 0:
        inputs_used.append("missing_time_horizon")
        return None, "No valid time horizon until deadline", tuple(inputs_used)

    # Edge case: expired deadline
    if time_to_deadline_days < 0:
        inputs_used.append("deadline_expired")
        if direction == "above":
            return 0.0, "Deadline expired — threshold not reached", tuple(inputs_used)
        else:
            return 1.0, "Deadline expired — threshold reached", tuple(inputs_used)

    # Calculate z-score using simplified GBM approximation
    # sigma_annual = daily_vol * sqrt(365)
    # z = (ln(threshold/current)) / (sigma_annual * sqrt(days/365))
    # Simplified: use price difference instead of log for clarity

    price_diff = threshold - current_price
    if direction == "above":
        is_above = price_diff > 0
    else:
        is_above = price_diff < 0

    # Annualized volatility from daily (assuming ~252 trading days)
    annualized_vol = historical_volatility * (252 ** 0.5)

    # Time fraction of year
    time_fraction = time_to_deadline_days / 365.0

    # Standard deviation of price change
    std_change = current_price * annualized_vol * (time_fraction ** 0.5)

    if std_change <= 0:
        inputs_used.append("zero_std_change")
        return None, "Invalid volatility/time combination yields zero std change", tuple(inputs_used)

    # Z-score (distance in std units)
    z = price_diff / std_change

    # Simplified CDF approximation (not precise, but transparent)
    # For |z| > 3, probability is essentially 0 or 1
    # For |z| < 0.5, probability is ~0.5
    # This is intentionally conservative — not a trading model

    if z > 3:
        if is_above:
            prob = 0.02  # Very unlikely to go that far up
        else:
            prob = 0.98  # Very likely to stay above
    elif z < -3:
        if is_above:
            prob = 0.98  # Very likely to reach that high
        else:
            prob = 0.02  # Very unlikely to go that far down
    elif z > 1.5:
        if is_above:
            prob = 0.20  # Unlikely but possible
        else:
            prob = 0.80  # Likely to stay
    elif z < -1.5:
        if is_above:
            prob = 0.80  # Likely to reach
        else:
            prob = 0.20  # Unlikely to go that far
    elif z > 0.5:
        if is_above:
            prob = 0.40  # Somewhat unlikely
        else:
            prob = 0.60  # Somewhat likely
    elif z < -0.5:
        if is_above:
            prob = 0.60  # Somewhat likely
        else:
            prob = 0.40  # Somewhat unlikely
    else:
        # Near threshold, probability is close to 0.5
        prob = 0.50 + z * 0.1  # Linear interpolation near 0

    # Clamp to reasonable bounds
    prob = max(0.01, min(0.99, prob))

    inputs_used.extend(["probabilistic_estimate", f"z_score={z:.2f}"])
    reason_parts = [
        f"Current: ${current_price:.2f}, Threshold: ${threshold:.2f}",
        f"Time: {time_to_deadline_days:.0f} days, Vol: {annualized_vol*100:.0f}% ann.",
    ]

    return prob, "; ".join(reason_parts), tuple(inputs_used)


def analyze_quant(
    text: str,
    event_type: str | None,
    proposition_status: str,
    threshold: float | None = None,
    asset: str | None = None,
    current_price: float | None = None,
    historical_volatility: float | None = None,
    deadline: str | None = None,
) -> QuantResult:
    """Main entry point: analyze price-threshold proposition.

    Args:
        text: The proposition text (for fallback parsing)
        event_type: Should be "price_above" or "price_below"
        proposition_status: "CLEAR" or "AMBIGUOUS"
        threshold: The price threshold (from parse_market_proposition)
        asset: CoinGecko coin ID (from parse_market_proposition.asset)
        current_price: Current underlying asset price (from external source)
        historical_volatility: Historical realized volatility (from external source)
        deadline: Deadline date string (for time-to-deadline calculation)

    Returns:
        QuantResult with probability if available, or available=False."""
    inputs_used: list[str] = []

    # Check if this model handles the event type
    if event_type not in ("price_above", "price_below"):
        return QuantResult(
            available=False,
            probability=None,
            confidence=0.0,
            data_quality=DataQualityBreakdown(
                vollstaendigkeit=0.0,
                aktualitaet=0.0,
                quellenuebereinstimmung=0.0,
                historische_fallzahl=0.0,
                resolution_klarheit=0.0,
                liquiditaet=0.0,
            ),
            reason=f"event_type '{event_type}' not handled by quant model (expected price_above/price_below)",
            inputs_used=(),
            contributions=(),
            uncertainty=1.0,
        )

    # Check if asset is supported
    if asset is None or asset.lower() not in _SUPPORTED_ASSETS:
        inputs_used.append("unsupported_asset")
        return QuantResult(
            available=False,
            probability=None,
            confidence=0.0,
            data_quality=DataQualityBreakdown(
                vollstaendigkeit=0.0,
                aktualitaet=0.0,
                quellenuebereinstimmung=0.0,
                historische_fallzahl=0.0,
                resolution_klarheit=0.0,
                liquiditaet=0.0,
            ),
            reason=f"Asset '{asset}' not supported (supported: {', '.join(sorted(_SUPPORTED_ASSETS))})",
            inputs_used=tuple(inputs_used),
            contributions=(),
            uncertainty=1.0,
        )

    # Check for threshold
    if threshold is None:
        inputs_used.append("no_threshold")
        return QuantResult(
            available=False,
            probability=None,
            confidence=0.0,
            data_quality=DataQualityBreakdown(
                vollstaendigkeit=0.0,
                aktualitaet=0.0,
                quellenuebereinstimmung=0.0,
                historische_fallzahl=0.0,
                resolution_klarheit=0.0,
                liquiditaet=0.0,
            ),
            reason="Threshold not parsed from proposition text",
            inputs_used=tuple(inputs_used),
            contributions=(),
            uncertainty=1.0,
        )

    # Check for current price (CRITICAL — cannot forecast without it)
    if current_price is None:
        inputs_used.append("missing_price_data")
        return QuantResult(
            available=False,
            probability=None,
            confidence=0.0,
            data_quality=DataQualityBreakdown(
                vollstaendigkeit=0.0,
                aktualitaet=0.0,
                quellenuebereinstimmung=0.0,
                historische_fallzahl=0.0,
                resolution_klarheit=0.0,
                liquiditaet=0.0,
            ),
            reason="Current underlying price data unavailable — cannot compute probabilistic estimate",
            inputs_used=tuple(inputs_used),
            contributions=(),
            uncertainty=1.0,
        )

    # Calculate time to deadline
    time_to_deadline_days: float | None = None
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            now = datetime.now(UTC)
            if deadline_dt.tzinfo is None:
                deadline_dt = deadline_dt.replace(tzinfo=UTC)
            if now.tzinfo is None:
                now = now.replace(tzinfo=UTC)
            delta = deadline_dt - now
            time_to_deadline_days = delta.total_seconds() / 86400
        except (ValueError, TypeError):
            inputs_used.append("deadline_parse_error")
            # Continue without time horizon — will be handled in estimation

    # Run estimation
    probability, reason, estimation_inputs = _estimate_price_probability(
        current_price=current_price,
        threshold=threshold,
        direction="above" if event_type == "price_above" else "below",
        time_to_deadline_days=time_to_deadline_days,
        historical_volatility=historical_volatility,
    )

    inputs_used.extend(estimation_inputs)

    if probability is None:
        return QuantResult(
            available=False,
            probability=None,
            confidence=0.0,
            data_quality=DataQualityBreakdown(
                vollstaendigkeit=0.0,
                aktualitaet=0.0,
                quellenuebereinstimmung=0.0,
                historische_fallzahl=0.0,
                resolution_klarheit=0.0,
                liquiditaet=0.0,
            ),
            reason=reason,
            inputs_used=tuple(inputs_used),
            contributions=(),
            uncertainty=1.0,
        )

    # Build data quality
    has_price = "threshold_already_crossed" in inputs_used or "probabilistic_estimate" in inputs_used
    has_volatility = historical_volatility is not None
    has_time = time_to_deadline_days is not None and time_to_deadline_days >= 0

    data_quality = DataQualityBreakdown(
        vollstaendigkeit=1.0 if (has_price and has_volatility and has_time) else 0.5,
        aktualitaet=1.0 if has_price else 0.0,
        quellenuebereinstimmung=0.5,
        historische_fallzahl=0.5 if has_volatility else 0.0,
        resolution_klarheit=1.0 if proposition_status == "CLEAR" else 0.5,
        liquiditaet=0.5,
    )

    # Confidence based on data quality and z-score magnitude
    if has_price and has_volatility and has_time:
        if "probabilistic_estimate" in inputs_used:
            # Extract z-score from inputs if available
            z_str = [i for i in inputs_used if i.startswith("z_score=")]
            if z_str:
                z_val = float(z_str[0].replace("z_score=", ""))
                if abs(z_val) > 2:
                    confidence = 65.0
                elif abs(z_val) > 1:
                    confidence = 50.0
                else:
                    confidence = 35.0
            else:
                confidence = 45.0
        else:
            confidence = 75.0  # Threshold already crossed
    else:
        confidence = 25.0

    uncertainty = max(0.0, 1.0 - confidence / 100.0)

    # Build contribution breakdown
    contributions: list[dict] = []
    for inp in inputs_used:
        if inp == "threshold_already_crossed_above":
            contributions.append({"source": "threshold_already_crossed", "weight": 0.4, "impact": "positive"})
        elif inp == "threshold_already_crossed_below":
            contributions.append({"source": "threshold_already_crossed", "weight": 0.4, "impact": "negative"})
        elif inp == "missing_price_data":
            contributions.append({"source": "price_data", "weight": 0.0, "impact": "missing"})
        elif inp == "missing_volatility":
            contributions.append({"source": "volatility_data", "weight": 0.0, "impact": "missing"})
        elif inp == "missing_time_horizon":
            contributions.append({"source": "time_horizon", "weight": 0.0, "impact": "missing"})
        elif inp == "deadline_expired":
            contributions.append({"source": "deadline_expired", "weight": 0.4, "impact": "neutral"})
        elif inp == "probabilistic_estimate":
            contributions.append({"source": "probabilistic_estimate", "weight": 0.3, "impact": "positive"})
        elif inp.startswith("z_score="):
            z_val = float(inp.replace("z_score=", ""))
            if abs(z_val) > 2:
                contributions.append({"source": "z_score_significant", "weight": 0.2, "impact": "strong"})
            else:
                contributions.append({"source": "z_score_modest", "weight": 0.1, "impact": "modest"})

    return QuantResult(
        available=True,
        probability=round(probability, 4),
        confidence=confidence,
        data_quality=data_quality,
        reason=reason,
        inputs_used=tuple(inputs_used),
        contributions=tuple(contributions),
        uncertainty=uncertainty,
    )


# Backward compatibility alias
def compute_quant_forecast(
    text: str,
    event_type: str | None,
    proposition_status: str,
    threshold: float | None = None,
    asset: str | None = None,
    current_price: float | None = None,
    historical_volatility: float | None = None,
    deadline: str | None = None,
) -> QuantResult:
    """Alias for analyze_quant — same interface, preserved for
    backward compatibility during the Phase E transition."""
    return analyze_quant(text, event_type, proposition_status, threshold, asset, current_price, historical_volatility, deadline)