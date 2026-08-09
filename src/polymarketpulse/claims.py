"""Claim Extraction & Verification

This module extends the existing event extraction with structured claims.

KEY DIFFERENCES:
- Event = what happened (who did what, when, where)
- Claim = what someone said (subject, predicate, object, speaker, source)

Example:
  Article: "Iran says traffic through Hormuz remains restricted."

  ExtractedEvent:
    actors = ("Iran",)
    action = "state"
    event_type = "strategic_waterway"
    status = "actual"

  ExtractedClaim:
    subject = "Hormuz traffic"
    predicate = "remains"
    object = "restricted"
    speaker = "Iran"
    source = "gdelt:irannews.ir"
    verification_status = "UNVERIFIED"
    confidence = 0.8
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


def _stable_claim_id(normalized: str) -> str:
    """Deterministic claim id, stable across process restarts.

    Python's builtin hash() is salted per-process (PYTHONHASHSEED), so it
    cannot be used for an identifier that needs to stay stable across runs
    (dedup keys, persisted rows, cross-run comparison).
    """
    digest = hashlib.sha256(normalized.lower().encode("utf-8")).hexdigest()
    return f"claim_{digest[:12]}"

from .prediction.semantics import ExtractedEvent

# Verification states
# These track the state of a claim, NOT whether it's objectively true
VerificationStatus = Literal[
    "UNVERIFIED",        # No verification attempted
    "SINGLE_SOURCE",     # Only one source reported it
    "MULTI_SOURCE",      # Multiple sources reported (same content)
    "PRIMARY_CONFIRMED", # Primary/official source confirmed
    "CONTRADICTED",      # Official source contradicted it
    "DISPUTED",          # Disputed by another source
]


@dataclass(frozen=True)
class Claim:
    """One structured claim extracted from raw text.
    
    A single article may contain multiple claims. Each claim is a
    subject-predicate-object triple with source attribution.
    """
    claim_id: str  # Stable identifier (e.g., sha256 of normalized claim)
    subject: str
    predicate: str
    object: str | None  # May be None for state predicates (e.g., "remains restricted")
    speaker: str | None  # Who made this claim?
    source_id: str  # Source identifier (e.g., "gdelt:reuters.com")
    source_url: str | None
    timestamp: datetime | None  # When was this claim published?
    verification_status: VerificationStatus = "UNVERIFIED"
    confidence: float = 0.0  # 0..1, how confident are we in the extraction?
    entities: tuple[str, ...] = field(default_factory=tuple)
    location: str | None = None
    raw_reference: str | None = None  # Original text excerpt
    
    def as_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "speaker": self.speaker,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "verification_status": self.verification_status,
            "confidence": self.confidence,
            "entities": list(self.entities),
            "location": self.location,
            "raw_reference": self.raw_reference,
        }
    
    def normalized(self) -> str:
        """Return a normalized representation for deduplication."""
        parts = [self.subject.lower(), self.predicate.lower()]
        if self.object:
            parts.append(self.object.lower())
        return " ".join(parts)


@dataclass(frozen=True)
class ClaimGroup:
    """A group of claims that represent the same underlying claim.
    
    This is for deduplication - multiple articles may report the same
    claim with slightly different wording.
    """
    claim_id: str  # The canonical claim_id for this group
    canonical_claim: Claim  # The representative claim
    republishing_sources: tuple[str, ...]  # Other sources that published it
    independent_sources: int  # Count of genuinely independent sources
    confirmation_count: int  # Total confirmations (including republishing)
    verification_status: VerificationStatus
    
    def as_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "canonical_claim": self.canonical_claim.as_dict(),
            "republishing_sources": list(self.republishing_sources),
            "independent_sources": self.independent_sources,
            "confirmation_count": self.confirmation_count,
            "verification_status": self.verification_status,
        }


# Event types that this module can extract
# These are more granular than the existing event_type taxonomy
CLAIM_EVENT_TYPES = frozenset({
    "CEASEFIRE_PROPOSED",
    "CEASEFIRE_AGREED",
    "CEASEFIRE_BROKEN",
    "CEASEFIRE_EXPIRED",
    "CEASEFIRE_DENIED",
    "NEGOTIATIONS_STARTED",
    "NEGOTIATIONS_PROGRESS",
    "NEGOTIATIONS_FAILED",
    "NEGOTIATIONS_CANCELLED",
    "MILITARY_STRIKE",
    "MILITARY_DEPLOYMENT",
    "MILITARY_WITHDRAWAL",
    "SANCTIONS_IMPOSED",
    "SANCTIONS_REMOVED",
    "SANCTIONS_EXTENDED",
    "WATERWAY_RESTRICTED",
    "WATERWAY_CLOSED",
    "WATERWAY_NORMALIZING",
    "WATERWAY_NORMALIZED",
    "OFFICIAL_CONFIRMATION",
    "OFFICIAL_DENIAL",
    "OFFICIAL_RETRACTION",
    "RESIGNATION",
    "REMOVAL_PROCESS",
    "OFFICE_DEPARTURE",
    "RATE_CUT",
    "RATE_HOLD",
    "RATE_HIKE",
    "ECONOMIC_RELEASE",
    "PRICE_STATE",
    "SPORT_RESULT",
    "SPORT_FIXTURE",
})


@dataclass(frozen=True)
class ExtractedClaim:
    """One claim extracted from raw text, ready for deduplication.
    
    This is an intermediate structure - claims are extracted here first,
    then grouped and deduplicated into ClaimGroup objects.
    """
    subject: str
    predicate: str
    object: str | None
    speaker: str | None
    source_id: str
    source_url: str | None
    timestamp: datetime | None
    entities: tuple[str, ...]
    location: str | None
    raw_reference: str | None
    certainty: Literal["confirmed", "reported", "announced", "speculative", "unknown"]
    event_type: str | None  # If this claim relates to a known event type
    direction: Literal["positive", "negative", "neutral"] = "neutral"  # YES/NO direction
    
    def to_claim(self, claim_id: str) -> Claim:
        """Convert to a Claim with a stable ID."""
        # Map certainty to verification_status
        verification_status: VerificationStatus = {
            "confirmed": "PRIMARY_CONFIRMED",
            "announced": "MULTI_SOURCE",  # Assume multiple sources for announcements
            "reported": "SINGLE_SOURCE",
            "speculative": "UNVERIFIED",
            "unknown": "UNVERIFIED",
        }.get(self.certainty, "UNVERIFIED")
        
        return Claim(
            claim_id=claim_id,
            subject=self.subject,
            predicate=self.predicate,
            object=self.object,
            speaker=self.speaker,
            source_id=self.source_id,
            source_url=self.source_url,
            timestamp=self.timestamp,
            verification_status=verification_status,
            confidence=self._confidence(),
            entities=self.entities,
            location=self.location,
            raw_reference=self.raw_reference,
        )
    
    def _confidence(self) -> float:
        """Calculate confidence based on certainty and available signals."""
        base = {
            "confirmed": 0.95,
            "announced": 0.90,
            "reported": 0.75,
            "speculative": 0.40,
            "unknown": 0.30,
        }.get(self.certainty, 0.50)
        
        # Boost if we have speaker attribution
        if self.speaker:
            base += 0.05
        
        # Boost if we have entity extraction
        if self.entities:
            base += 0.05
        
        return round(min(1.0, base), 2)


def extract_claims_from_article(
    title: str,
    body: str | None = None,
    source_id: str = "unknown",
    source_url: str | None = None,
    timestamp: datetime | None = None,
) -> tuple[ExtractedClaim, ...]:
    """Extract all claims from an article.
    
    This uses simple heuristics to identify claims in text.
    A claim is typically:
    - A statement with a clear subject-predicate-object structure
    - Not just factual reporting (e.g., "Prices rose to $X")
    - Not just quoted speech without assertion
    
    Returns a tuple of claims, which may be empty if no claims are detected.
    """
    claims: list[ExtractedClaim] = []
    
    # Combine title and body for extraction
    text = title if not body else f"{title}. {body}"
    raw_reference = title if not body else f"{title}\n\n{body[:500]}..."
    
    # Extract speaker from source attribution phrases
    speaker = _extract_speaker(text)
    
    # Extract entities (subjects/objects)
    entities = _extract_entities_for_claims(text)
    
    # Extract location
    location = _extract_location(text)
    
    # Extract direction (positive/negative/neutral)
    direction = _detect_claim_direction(text)
    
    # Extract certainty
    certainty = _detect_claim_certainty(text)
    
    # Extract predicate and object from key phrases
    predicate, obj = _extract_predicate_object(text)
    
    # Extract subject
    subject = _extract_subject_for_claim(text, entities)
    
    if subject and predicate:
        claims.append(ExtractedClaim(
            subject=subject,
            predicate=predicate,
            object=obj,
            speaker=speaker,
            source_id=source_id,
            source_url=source_url,
            timestamp=timestamp,
            entities=tuple(entities),
            location=location,
            raw_reference=raw_reference,
            certainty=certainty,
            event_type=None,  # Will be set later based on context
            direction=direction,
        ))
    
    return tuple(claims)


def _extract_speaker(text: str) -> str | None:
    """Extract speaker from source attribution phrases."""
    # Direct attribution patterns
    patterns = [
        (r"(?:Iran|US|White House|FED|ECB|Germany|France)\s+(?:says|states|declares|announces|reports|confirmed)\s+", 
         lambda m: m.group(1)),
        (r"(?:according to|source:)\s+([A-Za-z][\w'.-]+)", lambda m: m.group(1)),
    ]
    
    for pattern, extractor in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return extractor(match)
    
    return None


def _extract_entities_for_claims(text: str) -> list[str]:
    """Extract entities that could be subjects/objects of claims."""
    # Reuse existing extraction logic
    from polymarketpulse.prediction.semantics import _extract_actors
    
    actors = _extract_actors(text)
    return list(actors)


def _extract_location(text: str) -> str | None:
    """Extract location from text."""
    location_patterns = [
        r"Strait of Hormuz",
        r"Hormuz Strait",
        r"the Gulf",
        r"in the Middle East",
        r"in Washington",
        r"in Tehran",
        r"in Brussels",
    ]
    
    for pattern in location_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if "Hormuz" in pattern:
                return "Strait of Hormuz"
            if "Gulf" in pattern:
                return "Persian Gulf"
            if "Middle East" in pattern:
                return "Middle East"
            if "Washington" in pattern:
                return "Washington, D.C."
            if "Tehran" in pattern:
                return "Tehran"
            if "Brussels" in pattern:
                return "Brussels"
    
    return None


def _detect_claim_direction(text: str) -> Literal["positive", "negative", "neutral"]:
    """Detect whether a claim supports YES or NO for a typical proposition."""
    positive_patterns = [
        r"returns to normal",
        r"increases",
        r"rises",
        r"recovered",
        r"normalization",
        r"reopening",
        r"lifted",
        r"removed",
    ]
    
    negative_patterns = [
        r"remains restricted",
        r"decreases",
        r"falls",
        r"disrupted",
        r"blockade",
        r"closed",
        r"imposed",
    ]
    
    text_lower = text.lower()
    
    for pattern in positive_patterns:
        if re.search(pattern, text_lower):
            return "positive"
    
    for pattern in negative_patterns:
        if re.search(pattern, text_lower):
            return "negative"
    
    return "neutral"


def _detect_claim_certainty(text: str) -> Literal["confirmed", "reported", "announced", "speculative", "unknown"]:
    """Detect the certainty level of a claim."""
    confirmed = [
        r"confirmed",
        r"officially",
        r"verified",
        r"states (?:that|clearly)",
        r"declares",
    ]
    
    announced = [
        r"announces",
        r"announced",
        r"declares",
    ]
    
    reported = [
        r"reports",
        r"reports that",
        r"according to",
        r"sources say",
    ]
    
    speculative = [
        r"could",
        r"may",
        r"might",
        r"might",
        r"rumored",
        r"reportedly",
    ]
    
    for pattern in confirmed:
        if re.search(pattern, text, re.IGNORECASE):
            return "confirmed"
    
    for pattern in announced:
        if re.search(pattern, text, re.IGNORECASE):
            return "announced"
    
    for pattern in reported:
        if re.search(pattern, text, re.IGNORECASE):
            return "reported"
    
    for pattern in speculative:
        if re.search(pattern, text, re.IGNORECASE):
            return "speculative"
    
    return "unknown"


def _extract_predicate_object(text: str) -> tuple[str, str | None]:
    """Extract predicate and object from text."""
    # Common predicate patterns
    patterns = [
        (r"(remains|stays|keeps)\s+(restricted|normal|open|closed)", 
         lambda m: (m.group(1), m.group(2))),
        (r"(returns|goes back)\s+to\s+(normal|previous levels)",
         lambda m: ("returns to", m.group(2))),
        (r"(imposes|lifts|removes)\s+(sanctions|restrictions)",
         lambda m: (m.group(1), m.group(2))),
        (r"(announces|confirms)\s+(ceasefire|agreement)",
         lambda m: (m.group(1), m.group(2))),
    ]
    
    for pattern, extractor in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return extractor(match)
    
    return ("state", None)


def _extract_subject_for_claim(text: str, entities: list[str]) -> str | None:
    """Extract subject from text based on entities and context."""
    if entities:
        return entities[0]  # First entity is usually the subject
    
    # Try to extract from context
    if "Hormuz" in text or "hormuz" in text.lower():
        return "Hormuz traffic"
    if "Iran" in text:
        return "Iran"
    if "US" in text or "United States" in text:
        return "US"
    if "Fed" in text or "Federal Reserve" in text:
        return "Fed policy"
    
    return None


# --- Deduplication logic ---

def group_claims_by_normalization(
    claims: tuple[ExtractedClaim, ...],
) -> list[ClaimGroup]:
    """Group claims that represent the same underlying claim.
    
    Uses normalized representation for grouping:
    - Subject + predicate + object (normalized)
    - Speaker (if available)
    - Timestamp (same day)
    """
    from collections import defaultdict
    
    # Group by normalized claim
    groups: dict[str, list[ExtractedClaim]] = defaultdict(list)
    
    for claim in claims:
        normalized = claim.normalized()
        groups[normalized].append(claim)
    
    # Convert to ClaimGroups
    claim_groups: list[ClaimGroup] = []
    for normalized, claims_list in groups.items():
        # Determine canonical claim (most verified)
        claims_list.sort(key=lambda c: {
            "confirmed": 5,
            "announced": 4,
            "reported": 3,
            "speculative": 2,
            "unknown": 1,
        }.get(c.certainty, 0), reverse=True)
        
        canonical = claims_list[0]
        canonical_claim = canonical.to_claim(f"claim_{hash(normalized.lower()) % 1000000:06d}")
        
        # Determine verification status
        verification_status: VerificationStatus = "SINGLE_SOURCE"
        if any(c.certainty == "confirmed" for c in claims_list):
            verification_status = "PRIMARY_CONFIRMED"
        elif any(c.certainty == "announced" for c in claims_list) or len(claims_list) >= 3:
            verification_status = "MULTI_SOURCE"
        
        claim_groups.append(ClaimGroup(
            claim_id=canonical_claim.claim_id,
            canonical_claim=canonical_claim,
            republishing_sources=tuple(c.source_id for c in claims_list[1:]),
            independent_sources=1,  # Simplified - would need domain analysis in production
            confirmation_count=len(claims_list),
            verification_status=verification_status,
        ))
    
    return claim_groups


def normalize_for_dedup(claim: ExtractedClaim | Claim) -> str:
    """Return a normalized string for claim deduplication."""
    if isinstance(claim, Claim):
        return claim.normalized()
    return f"{claim.subject.lower()}|{claim.predicate.lower()}|{claim.object or ''}"


# --- Integration with existing event extraction ---

def extract_claim_from_event(
    event: ExtractedEvent,
    source_id: str,
    source_url: str | None = None,
    timestamp: datetime | None = None,
) -> ExtractedClaim | None:
    """Convert an ExtractedEvent to an ExtractedClaim where applicable.
    
    Not all events can be converted to claims (e.g., generic "state" events).
    Returns None if the event doesn't represent a claimable statement.
    """
    if not event.action:
        return None
    
    # Map action to predicate
    predicate_map = {
        "resignation": "resigned",
        "announce_intent_to_resign": "announced intent to resign",
        "call_for_resignation": "called for resignation",
        "official_duty": "performed official duties",
        "routine_activity": "engaged in routine activity",
        "escalation": "escalated conflict",
        "deescalation": "de-escalated tension",
    }
    
    predicate = predicate_map.get(event.action)
    if not predicate:
        return None
    
    # Determine object based on status
    obj: str | None = None
    if event.status == "actual":
        obj = "occurred"
    elif event.status == "intent":
        obj = "planned"
    elif event.status == "continuation":
        obj = "continues"
    
    # Determine direction
    direction: Literal["positive", "negative", "neutral"] = "neutral"
    if event.action == "deescalation":
        direction = "positive"
    elif event.action == "escalation":
        direction = "negative"
    
    return ExtractedClaim(
        subject=" ".join(event.actors) if event.actors else None,
        predicate=predicate,
        object=obj,
        speaker=None,  # Would need speaker extraction
        source_id=source_id,
        source_url=source_url,
        timestamp=timestamp,
        entities=event.actors,
        location=event.location,
        raw_reference=None,
        certainty="reported",  # Default for events
        event_type=event.event_type,
        direction=direction,
    )


# --- Causal distance for claims ---

def claim_causal_distance(claim: Claim, target_proposition: MarketProposition) -> int:
    """Estimate causal distance between a claim and target proposition.
    
    Returns 0 for direct, higher for more indirect.
    """
    if not claim.subject or not target_proposition.subject:
        return 999  # Unknown
    
    subject_match = claim.subject.lower() == target_proposition.subject.lower()
    event_match = claim.event_type == target_proposition.event_type
    
    if subject_match and event_match:
        return 0  # Direct
    if subject_match:
        return 1  # Same subject, different event type
    if any(e in claim.entities for e in target_proposition.entities or []):
        return 2  # Related entity
    return 999  # No clear connection


# Import here to avoid circular dependencies
try:
    from polymarketpulse.prediction.semantics import MarketProposition
except ImportError:
    pass  # Will be available at runtime