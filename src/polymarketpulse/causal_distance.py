"""Causal Distance Framework

Introduces explicit causal distance to prevent speculative graph edges from
moving probabilities. Implements the "Causal Distance" principle from Phase O:

- Every relationship has a causal distance (0 = direct, higher = more indirect)
- Farther causal distance means lower quantitative influence
- Never silently invent intermediate events
- Display causal chains for explainability

This ensures that evidence like:
  "oil prices falling" 
does not automatically become strong evidence for:
  "Strait traffic returns to normal"
unless there is a verified causal chain connecting them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal


# Causal distance tiers (IntEnum for clear ordering: 0 is most direct)
class CausalDistance(IntEnum):
    """Causal distance between evidence and target outcome.
    
    Distance 0: Direct causal link (no intermediate steps)
    Distance 1: One intermediate step
    Distance 2: Two intermediate steps
    Distance 3+: Increasingly indirect/speculative
    
    Rules (documented):
      - Distance 0: Direct observation/state change (e.g., "waterway reopened")
      - Distance 1: Immediate consequence (e.g., "shipping traffic increasing")
      - Distance 2: Secondary effect (e.g., "insurance premiums falling")
      - Distance 3: Tertiary effect (e.g., "oil prices falling")
      - Distance 4+: Highly speculative, likely irrelevant
    
    Implementation note: Distance is assigned at event extraction time
    based on the event type and context, not computed dynamically.
    """
    DIRECT = 0          # Direct causal link
    IMMEDIATE = 1       # One intermediate step
    SECONDARY = 2       # Two intermediate steps
    TERTIARY = 3        # Three intermediate steps
    SPECULATIVE = 4     # Highly speculative
    UNKNOWN = 999       # Unknown distance


# Distance decay factors (per distance tier)
# These determine how much weight to give evidence at each distance level
DISTANCE_DECAY = {
    CausalDistance.DIRECT: 1.0,
    CausalDistance.IMMEDIATE: 0.7,
    CausalDistance.SECONDARY: 0.4,
    CausalDistance.TERTIARY: 0.2,
    CausalDistance.SPECULATIVE: 0.05,
    CausalDistance.UNKNOWN: 0.5,  # Neutral if unknown
}


@dataclass(frozen=True)
class CausalChainStep:
    """One step in a causal chain from evidence to target."""
    event_type: str  # e.g., "CEASEFIRE_ANNOUNCED", "PORT_REOPENED"
    actors: tuple[str, ...]  # Who caused this?
    entities: tuple[str, ...]  # What was affected?
    direction: Literal["positive", "negative"]  # Direction toward target
    confidence: float  # Confidence in this step (0..1)
    evidence_tier: str  # KNOWN, STRONG_EVIDENCE, SUPPORTED, etc.
    distance: CausalDistance
    
    def as_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "actors": list(self.actors),
            "entities": list(self.entities),
            "direction": self.direction,
            "confidence": self.confidence,
            "evidence_tier": self.evidence_tier,
            "causal_distance": self.distance.value,
        }


@dataclass(frozen=True)
class CausalChain:
    """Complete causal chain from initial event to target outcome."""
    chain_id: str
    target_outcome: str  # What we're predicting
    initial_event: CausalChainStep
    steps: tuple[CausalChainStep, ...]
    total_distance: CausalDistance
    max_confidence: float
    overall_confidence: float
    is_plausible: bool  # Can this chain support a forecast?
    
    def as_dict(self) -> dict:
        return {
            "chain_id": self.chain_id,
            "target_outcome": self.target_outcome,
            "initial_event": self.initial_event.as_dict(),
            "steps": [s.as_dict() for s in self.steps],
            "total_distance": self.total_distance.value,
            "max_confidence": self.max_confidence,
            "overall_confidence": self.overall_confidence,
            "is_plausible": self.is_plausible,
        }
    
    @property
    def weight(self) -> float:
        """Calculate effective weight based on distance and confidence."""
        decay = DISTANCE_DECAY[self.total_distance]
        return round(self.overall_confidence * decay, 4)


@dataclass(frozen=True)
class CausalAnalysis:
    """Complete causal analysis for one market proposition."""
    market_id: str
    proposition: str
    chains: tuple[CausalChain, ...]
    direct_evidence_count: int  # Distance 0 evidence
    indirect_evidence_count: int  # Distance > 0 evidence
    plausible_chains_count: int
    
    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "proposition": self.proposition,
            "chains": [c.as_dict() for c in self.chains],
            "direct_evidence": self.direct_evidence_count,
            "indirect_evidence": self.indirect_evidence_count,
            "plausible_chains": self.plausible_chains_count,
        }


# Common causal patterns (for event type classification)
# These help determine causal distance at extraction time
COMMON_CAUSAL_PATTERNS = {
    # Direct causal patterns (distance 0)
    "waterway_reopened": CausalDistance.DIRECT,
    "ceasefire_signed": CausalDistance.DIRECT,
    "sanctions_lifted": CausalDistance.DIRECT,
    "port_closed": CausalDistance.DIRECT,
    "attack_occurred": CausalDistance.DIRECT,
    
    # Immediate consequence (distance 1)
    "shipping_traffic_increasing": CausalDistance.IMMEDIATE,
    "insurance_premiums_falling": CausalDistance.SECONDARY,  # Often 2 steps
    " tanker_traffic_increasing": CausalDistance.IMMEDIATE,
    "military_deployment_started": CausalDistance.IMMEDIATE,
    
    # Secondary effects (distance 2)
    "shipping_risk_decreasing": CausalDistance.SECONDARY,
    
    # Tertiary effects (distance 3)
    "oil_prices_falling": CausalDistance.TERTIARY,
    "market_volatility_decreasing": CausalDistance.TERTIARY,
    
    # Highly speculative (distance 4+)
    "public_sentiment_improving": CausalDistance.SPECULATIVE,
    "election_outcome_affected": CausalDistance.SPECULATIVE,
}


def determine_causal_distance(event_type: str, context: str | None = None) -> CausalDistance:
    """Determine causal distance for an event type based on known patterns.
    
    If event_type is not in COMMON_CAUSAL_PATTERNS, falls back to UNKNOWN.
    
    This is a heuristic - real causal distance should be determined by
    human annotation or more sophisticated analysis in the future.
    """
    # Try exact match first
    if event_type in COMMON_CAUSAL_PATTERNS:
        return COMMON_CAUSAL_PATTERNS[event_type]
    
    # Try partial match
    for pattern, distance in COMMON_CAUSAL_PATTERNS.items():
        if pattern in event_type.lower():
            return distance
    
    # Default to UNKNOWN if not recognized
    return CausalDistance.UNKNOWN


def create_causal_chain(
    chain_id: str,
    target_outcome: str,
    initial_event: CausalChainStep,
    steps: tuple[CausalChainStep, ...],
) -> CausalChain:
    """Create a causal chain from individual steps.
    
    Calculates total distance (max of all steps) and overall confidence
    (product of step confidences, capped by distance decay).
    """
    all_steps = (initial_event,) + steps
    total_distance = max(s.distance for s in all_steps)
    max_confidence = max(s.confidence for s in all_steps)
    
    # Overall confidence is product of all step confidences
    # (conservative: each step must be true)
    confidence_product = 1.0
    for step in all_steps:
        confidence_product *= step.confidence
    overall_confidence = round(confidence_product, 4)
    
    # Plausible if:
    # - Total distance <= 2 (direct or immediate consequence)
    # - Overall confidence >= 0.5
    # - All steps have evidence_tier at least SUPPORTED
    is_plausible = (
        total_distance <= CausalDistance.SECONDARY and
        overall_confidence >= 0.5 and
        all(s.evidence_tier in ("KNOWN", "STRONG_EVIDENCE", "SUPPORTED") for s in all_steps)
    )
    
    return CausalChain(
        chain_id=chain_id,
        target_outcome=target_outcome,
        initial_event=initial_event,
        steps=steps,
        total_distance=total_distance,
        max_confidence=max_confidence,
        overall_confidence=overall_confidence,
        is_plausible=is_plausible,
    )


def calculate_effective_weight(
    base_weight: float,
    causal_distance: CausalDistance,
    confidence: float,
) -> float:
    """Calculate effective weight after applying causal distance decay.
    
    This ensures that distant/indirect evidence cannot strongly influence
    the final probability.
    
    Formula: effective_weight = base_weight * decay_factor * confidence
    
    Where decay_factor is from DISTANCE_DECAY dict.
    """
    decay = DISTANCE_DECAY[causal_distance]
    return round(base_weight * decay * confidence, 4)


def format_causal_chain(chain: CausalChain) -> str:
    """Format causal chain as human-readable German text."""
    lines = [
        f"Kausale Kette: {chain.chain_id}",
        f"Ziel: {chain.target_outcome}",
        f"Distanz: {chain.total_distance.value} ({chain.total_distance.name})",
        f"Max. Konfidenz: {chain.max_confidence:.0%}",
        f"Gesamtkonfidenz: {chain.overall_confidence:.0%}",
        f"Plausibel: {'Ja' if chain.is_plausible else 'Nein'}",
        "",
        "Kette:",
    ]
    
    lines.append(f"  [D{chain.initial_event.distance.value}] {chain.initial_event.event_type} ({', '.join(chain.initial_event.actors)})")
    for step in chain.steps:
        lines.append(f"  [D{step.distance.value}] {step.event_type} ({', '.join(step.actors)})")
    
    return "\n".join(lines)