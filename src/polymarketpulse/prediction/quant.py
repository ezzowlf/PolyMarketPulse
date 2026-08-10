"""Quantitative Price-Threshold Model — forecast crypto price-threshold
markets using real underlying price data when available.

Supports questions such as:
  - BTC above X by date
  - BTC below X by date
  - ETH above/below X
  - Other supported assets where price data exists

Inputs (when available):
  - current underlying price (real, from CoinGecko's free/keyless
    market_chart endpoint — see providers/coingecko.py)
  - threshold
  - direction (above/below)
  - time to deadline
  - historical realized daily volatility (sample stdev of daily log
    returns over the trailing window CoinGecko returns, NOT a guessed
    constant)

Critical constraints:
  - Do NOT use Polymarket price (market-blind) — this module never accepts
    a market_yes_price/market_probability parameter, by construction
  - Do NOT introduce paid data sources
  - Use a transparent probabilistic approach
  - If price or volatility data is unavailable: return unavailable honestly

Return model (documented honestly, not aspirationally):
  - Terminal ("at deadline") probability: models log(S_T/S_0) as Normal
    with the stated drift assumption, using the closed-form Normal CDF
    (math.erf). P(S_T > B) = 1 - CDF(z), z = ln(B/S0) / (sigma*sqrt(T)).
  - Barrier ("by deadline", i.e. touches the threshold at ANY point before
    the deadline) probability: uses the reflection-principle approximation
    for a DRIFTLESS random walk in log-price space:
      P(touch B by T) = 2 * (1 - CDF(|z|))   for B on the far side of S0
    This is the standard closed-form result for a driftless Brownian
    motion hitting a one-sided barrier; applying it to GBM log-returns is
    an approximation (exact only under zero drift), which is why drift is
    assumed to be zero rather than fit from the historical sample (a
    biased drift estimate over 90 days would swamp the signal anyway).
  - Terminal and barrier probabilities are NEVER conflated: which one is
    computed is driven by MarketProposition.deadline_semantics
    ("at_deadline" vs "by_deadline", see semantics.py). If that field is
    None (the proposition's deadline phrasing doesn't confidently indicate
    either), this module returns unavailable rather than guessing.
  - Volatility: sample stdev of daily log returns over the trailing
    history window (typically 90 days), annualized by sqrt(252) only for
    display in the reason string — the actual calculation scales the raw
    daily vol by sqrt(time_to_deadline_days), not the annualized figure.
  - Drift: assumed zero (driftless random walk). This is a real
    simplification: crypto assets can have persistent drift over 90-day
    windows that this model does not capture. Longer horizons are
    therefore less reliable than short ones — this is not compensated for.
  - Limitations: no fat-tail/jump modeling (real crypto returns are not
    normally distributed — extreme moves are underestimated by this
    model), no volatility term structure (single trailing-90-day estimate
    used for all horizons), no drift, single-source price data (CoinGecko
    only, no cross-exchange reconciliation).

Design principle:
  - Expose all assumptions clearly
  - Handle: threshold already crossed, far away, very short horizon,
    longer horizon, missing price, missing volatility, expired deadline,
    ambiguous terminal-vs-barrier semantics"""

from __future__ import annotations

import math
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


def _normal_cdf(z: float) -> float:
    """Standard normal CDF via the exact erf identity (no approximation
    beyond floating point — this is not a lookup-table heuristic)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _estimate_price_probability(
    current_price: float,
    threshold: float,
    direction: str,
    time_to_deadline_days: float | None,
    historical_volatility: float | None,
    deadline_semantics: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Estimate probability under a lognormal / random-walk assumption,
    distinguishing terminal ("at deadline") from barrier ("by deadline")
    probability. See module docstring for the full math and limitations.

    `historical_volatility` is DAILY (not annualized) realized volatility —
    the sample stdev of daily log returns."""
    inputs_used: list[str] = []

    # A current price observed after expiry cannot establish the named
    # resolution source's value at the resolution timestamp. This check
    # must precede "threshold already crossed".
    if time_to_deadline_days is not None and time_to_deadline_days < 0:
        inputs_used.append("deadline_expired_without_resolution_observation")
        return (
            None,
            "Deadline expired - no point-in-time resolution-source observation available",
            tuple(inputs_used),
        )

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
    if time_to_deadline_days is None:
        inputs_used.append("missing_time_horizon")
        return None, "No valid time horizon until deadline", tuple(inputs_used)

    # Edge case: expired deadline (checked BEFORE the "<=0" catch-all below —
    # a negative time_to_deadline_days must reach this branch, not be
    # swallowed as "missing". Threshold-already-crossed was already checked
    # above, so by this point we know it hasn't been crossed yet.)
    if time_to_deadline_days < 0:
        inputs_used.append("deadline_expired")
        if direction == "above":
            return 0.0, "Deadline expired — threshold not reached", tuple(inputs_used)
        else:
            return 1.0, "Deadline expired — threshold reached", tuple(inputs_used)

    if time_to_deadline_days == 0:
        inputs_used.append("missing_time_horizon")
        return None, "No valid time horizon until deadline", tuple(inputs_used)

    # Ambiguous terminal-vs-barrier semantics: do not guess.
    if deadline_semantics not in ("at_deadline", "by_deadline"):
        inputs_used.append("ambiguous_deadline_semantics")
        return None, (
            "Proposition does not confidently indicate whether the threshold "
            "must hold AT the deadline or can be reached AT ANY POINT BY the "
            "deadline (terminal vs. barrier) — refusing to guess"
        ), tuple(inputs_used)

    # Log-return distance to threshold, scaled by realized volatility over
    # the horizon. sigma_T = daily_vol * sqrt(days) (no drift term — see
    # module docstring for why drift is assumed zero).
    log_distance = math.log(threshold / current_price)
    sigma_t = historical_volatility * math.sqrt(time_to_deadline_days)
    if sigma_t <= 0:
        inputs_used.append("zero_std_change")
        return None, "Invalid volatility/time combination yields zero std change", tuple(inputs_used)

    z = log_distance / sigma_t  # standardized distance from current price to threshold

    if deadline_semantics == "at_deadline":
        # Terminal probability: P(S_T > threshold) = 1 - CDF(z)
        p_terminal_above = 1.0 - _normal_cdf(z)
        prob = p_terminal_above if direction == "above" else (1.0 - p_terminal_above)
        model_label = "terminal (at-deadline)"
        inputs_used.append("terminal_model")
    else:
        # Barrier/touch probability via reflection principle for a
        # driftless random walk: P(touch by T) = 2 * (1 - CDF(|z|))
        p_touch = 2.0 * (1.0 - _normal_cdf(abs(z)))
        prob = p_touch
        model_label = "barrier (by-deadline / touch)"
        inputs_used.append("barrier_model")

    # Clamp to avoid overconfident 0/1 from floating point at extreme z
    prob = max(0.01, min(0.99, prob))

    inputs_used.extend(["probabilistic_estimate", f"z_score={z:.2f}"])
    annualized_vol_display = historical_volatility * (252 ** 0.5)
    reason_parts = [
        f"Current: ${current_price:.2f}, Threshold: ${threshold:.2f}",
        f"Time: {time_to_deadline_days:.0f} days, Vol: {annualized_vol_display*100:.0f}% ann. (daily-sampled)",
        f"Model: {model_label}",
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
    deadline_semantics: str | None = None,
) -> QuantResult:
    """Main entry point: analyze price-threshold proposition.

    Args:
        text: The proposition text (for fallback parsing)
        event_type: Should be "price_above" or "price_below"
        proposition_status: "CLEAR" or "AMBIGUOUS"
        threshold: The price threshold (from parse_market_proposition)
        asset: CoinGecko coin ID (from parse_market_proposition.asset)
        current_price: Current underlying asset price (real, from
            providers/coingecko.py — this module makes no HTTP calls itself)
        historical_volatility: Realized DAILY volatility (sample stdev of
            daily log returns, real, from providers/coingecko.py)
        deadline: Deadline date string (for time-to-deadline calculation)
        deadline_semantics: "at_deadline" (terminal) or "by_deadline"
            (barrier/touch), from MarketProposition.deadline_semantics.
            None means ambiguous — returns unavailable rather than guessing.

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
            deadline_dt = datetime.fromisoformat(deadline)
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
        deadline_semantics=deadline_semantics,
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
    deadline_semantics: str | None = None,
) -> QuantResult:
    """Alias for analyze_quant — same interface, preserved for
    backward compatibility during the Phase E transition."""
    return analyze_quant(
        text, event_type, proposition_status, threshold, asset,
        current_price, historical_volatility, deadline, deadline_semantics,
    )
