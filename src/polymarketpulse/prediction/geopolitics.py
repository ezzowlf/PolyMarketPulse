"""Geopolitics Model — forecast conflict-related markets using structured
semantic evidence, official sources, and historical comparables.

Handles event types:
  - ceasefire, war_escalation, military_action, sanctions, territorial_control,
    strategic_waterway, diplomatic_agreement

Inputs:
  - structured semantic evidence (proposition/event parsing)
  - official-source evidence (resolution authority, confirmed statements)
  - historical comparable baseline
  - deadline/time horizon
  - verified event state

Design principle:
  - NO generic positive/negative sentiment as forecast signal
  - If insufficient evidence: available=False
  - Always return structured result with data_quality and uncertainty"""

from __future__ import annotations

from dataclasses import dataclass

from .types import DataQualityBreakdown

# Event types this model handles
_EVENT_TYPES = frozenset({
    "ceasefire", "war_escalation", "military_action", "sanctions",
    "territorial_control", "strategic_waterway", "diplomatic_agreement",
})

# Official sources that count as strong evidence
_OFFICIAL_SOURCES = frozenset({
    "government", "official", "president", "prime minister", "minister",
    "spokesperson", "white house", "kremlin", "cabinet", "parliament",
    "un", "united nations", "nato", "eu", "european union",
})

# Direct vs. indirect evidence markers
_DIRECT_VERB_TERMS = frozenset({
    "announced", "declared", "confirmed", "states", "says", "reported",
    "officially", "verified", "signed", "agreed", "reached",
})

_INDIRECT_TERMS = frozenset({
    "reports", "sources say", "alleges", "claims", "according to",
    "rumored", "unconfirmed", "speculation",
})

# Negation/failure markers (the event did NOT happen)
_NEGATION_TERMS = frozenset({
    "denied", "denies", "rejected", "rejects", "fails", "failed",
    "failure", "collapse", "collapses", "collapsed", "breaks down",
    "broke down", "postponed", "delayed", "cancelled", "canceled",
    "ruled out", "backs down", "backed down", "not confirmed",
})


@dataclass(frozen=True)
class GeopoliticsResult:
    """Result of the geopolitical forecast model.

    available=False means the model could not form a judgment — not that
    the event is impossible, just that the input data was insufficient or
    incompatible with this model's scope."""

    available: bool
    probability: float | None
    confidence: float
    data_quality: DataQualityBreakdown
    reason: str
    inputs_used: tuple[str, ...]
    contributions: tuple[dict, ...]  # structured breakdown for audit
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


def _analyze_ceasefire(text: str, proposition_status: str) -> tuple[float | None, str, tuple[str, ...]]:
    """Ceasefire markets: is a ceasefire agreement actually in place?

    Key signals:
      - Direct official announcement (strong YES)
      - Withdrawal of troops/withdrawal confirmed (strong YES)
      - Peace talks only (weak/no signal — not yet an agreement)
      - Ceasefire denied/collapse (strong NO)

    The proposition's event_type alone is not enough — we need to know
    whether the ceasefire actually happened or not."""
    lowered = text.lower()

    # Strong YES signals (actual ceasefire in place)
    direct_yes_terms = frozenset({
        "ceasefire agreed", "ceasefire reached", "ceasefire deal",
        " ceasefire in place", "ceasefire holds", "ceasefire effective",
        "ceasefire announced", "ceasefire confirmed", "ceasefire signed",
        "ceasefire in effect", "ceasefire accepted", "ceasefire accepted by",
        "troops withdraw", "troops withdrawal", "troops pulled back",
        "truce in place", "truce agreed", "peace agreement signed",
        "peace deal reached", "armistice signed",
    })
    # Strong NO signals (ceasefire denied or collapsed)
    direct_no_terms = frozenset({
        "ceasefire denied", "ceasefire rejected", "ceasefire collapse",
        "ceasefire collapses", "ceasefire failed", "ceasefire falls apart",
        "ceasefire broke down", "ceasefire talks collapse", "ceasefire talks failed",
        "no ceasefire", "ceasefire not",
    })
    # Weak signals (talks only — not yet an actual agreement)
    talks_terms = frozenset({
        "peace talks", "ceasefire talks", "ceasefire negotiation",
        "ceasefire discussion", "ceasefire meeting", "ceasefire proposal",
        "ceasefire suggestion", "ceasefire consideration",
    })

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    # Direct YES — actual ceasefire in place
    if any(term in lowered for term in direct_yes_terms):
        # Check for negation (e.g. "ceasefire denied" is NO, not YES)
        if any(term in lowered for term in _NEGATION_TERMS):
            reason_parts.append("ceasefire denied/rejected despite positive phrasing")
            return 0.0, "ceasefire denied or collapsed", tuple(inputs_used)
        inputs_used.extend(["ceasefire_actual", "direct_statement"])
        reason_parts.append("direct official confirmation of ceasefire in place")
        # High confidence if from official source
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.append("official_source")
            return 0.85, "confirmed ceasefire agreement in place (official source)", tuple(inputs_used)
        return 0.70, "confirmed ceasefire agreement in place", tuple(inputs_used)

    # Direct NO — ceasefire denied or collapsed
    if any(term in lowered for term in direct_no_terms):
        inputs_used.append("ceasefire_denied")
        reason_parts.append("ceasefire denied, collapsed, or rejected")
        return 0.10, "ceasefire denied or collapsed", tuple(inputs_used)

    # Talks only — not yet an agreement (no forecast)
    if any(term in lowered for term in talks_terms):
        if not any(term in lowered for term in direct_yes_terms):
            inputs_used.append("ceasefire_talks_only")
            reason_parts.append("only peace talks reported, no agreement yet")
            return None, "only ceasefire talks reported, no agreement yet", tuple(inputs_used)

    # Proposition status AMBIGUOUS — could not parse the question
    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine ceasefire status", tuple(inputs_used)

    # No clear signal found
    inputs_used.append("insufficient_signal")
    reason_parts.append("no clear ceasefire status in proposition text")
    return None, "insufficient structured evidence for ceasefire determination", tuple(inputs_used)


def _analyze_war_escalation(text: str, proposition_status: str) -> tuple[float | None, str, tuple[str, ...]]:
    """War escalation markets: is conflict intensifying?

    Key signals:
      - Direct escalation verbs (strikes, offensive, invasion, mobilization)
      - Official confirmation of escalation
      - Withdrawal of de-escalation terms (weak NO signal)

    Important: "escalation" in the question does not mean YES.
    The market asks whether escalation will happen — we must determine
    whether it has already happened or not based on the proposition text."""
    lowered = text.lower()

    # Strong YES signals (escalation has occurred)
    escalation_terms = frozenset({
        "escalates", "escalation", "intensifies", "intensify", "offensive launched",
        "offensive begins", "attack", "strikes", "airstrike", "shelling",
        "invasion", "mobilizes", "mobilize", "military intervention",
        "war breaks out", "war begins", "war erupts", "combat intensifies",
    })

    # De-escalation terms (counter-signal)
    deescalation_terms = frozenset({
        "de-escalate", "de-escalation", "withdrawal", "withdraws troops",
        "ceasefire", "truce", "peace talks", "cease-fire",
    })

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    # Check for actual escalation
    if any(term in lowered for term in escalation_terms):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["escalation_actual", "official_source"])
            return 0.80, "confirmed military escalation (official source)", tuple(inputs_used)
        inputs_used.append("escalation_actual")
        return 0.65, "reported military escalation", tuple(inputs_used)

    # De-escalation counter-signal (means escalation is NOT happening)
    if any(term in lowered for term in deescalation_terms):
        if any(term in lowered for term in _DIRECT_VERB_TERMS):
            inputs_used.append("deescalation_actual")
            reason_parts.append("de-escalation confirmed (means escalation did not occur)")
            return 0.15, "de-escalation confirmed — escalation did not occur", tuple(inputs_used)

    # Proposition status AMBIGUOUS
    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine escalation status", tuple(inputs_used)

    # No clear signal
    inputs_used.append("insufficient_signal")
    reason_parts.append("no clear escalation or de-escalation status")
    return None, "insufficient structured evidence for escalation determination", tuple(inputs_used)


def _analyze_military_action(text: str, proposition_status: str) -> tuple[float | None, str, tuple[str, ...]]:
    """Military action markets: has specific military action occurred?

    Similar to escalation but more specific — often about a particular
    action (strike, intervention, operation)."""
    lowered = text.lower()

    action_terms = frozenset({
        "military strike", "airstrike", "bombardment", "ground invasion",
        "military intervention", "military operation", "troops deployed",
        "troops entered", "attack launched", "offensive launched",
        "artillery shelling", "missile strike", "combat operation",
    })
    deescalation_terms = frozenset({
        "withdrawal", "withdraws", "pull back", "ceasefire", "de-escalation",
    })

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    if any(term in lowered for term in action_terms):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["action_confirmed", "official_source"])
            return 0.75, "confirmed military action (official source)", tuple(inputs_used)
        inputs_used.append("action_confirmed")
        return 0.60, "reported military action", tuple(inputs_used)

    # De-escalation counter-signal
    if any(term in lowered for term in deescalation_terms):
        inputs_used.append("deescalation_counter")
        return 0.20, "de-escalation reported — military action unlikely", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine military action status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for military action determination", tuple(inputs_used)


def _analyze_sanctions(text: str, proposition_status: str) -> tuple[float | None, str, tuple[str, ...]]:
    """Sanctions markets: have sanctions been imposed?

    Key: sanctions are a political decision — look for official
    announcements, not just speculation."""
    lowered = text.lower()

    sanction_terms = frozenset({
        "sanctions imposed", "sanctions announced", "sanctions approved",
        "sanctions enacted", "sanctions passed", "sanctions against",
        "impose sanctions", "announce sanctions", "sanctions package",
        "travel ban", "asset freeze", "economic sanctions",
    })
    lifted_terms = frozenset({
        "sanctions lifted", "sanctions removed", "sanctions eased",
        "sanctions relaxed", "sanctions suspended", "sanctions waived",
    })

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    if any(term in lowered for term in sanction_terms):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["sanctions_imposed", "official_source"])
            return 0.85, "sanctions imposed (official source)", tuple(inputs_used)
        inputs_used.append("sanctions_imposed")
        return 0.70, "sanctions imposed", tuple(inputs_used)

    if any(term in lowered for term in lifted_terms):
        inputs_used.append("sanctions_lifted")
        return 0.15, "sanctions lifted or eased", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine sanctions status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for sanctions determination", tuple(inputs_used)


def _analyze_territorial_control(text: str, proposition_status: str) -> tuple[float | None, str, tuple[str, ...]]:
    """Territorial control markets: who controls a specific territory?

    Look for statements about control, occupation, capture, retaking."""
    lowered = text.lower()

    control_terms = frozenset({
        "control gained", "control established", "controls territory",
        "occupies territory", "takes control", "seizes territory",
        "captures territory", "establishes control", "territory under",
    })
    loss_terms = frozenset({
        "control lost", "control ceded", "loses territory", "ceded territory",
        "territory lost", "withdraws from territory", "relinquishes control",
    })

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    if any(term in lowered for term in control_terms):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["territory_controlled", "official_source"])
            return 0.80, "territory under control (official source)", tuple(inputs_used)
        inputs_used.append("territory_controlled")
        return 0.65, "territory under control", tuple(inputs_used)

    if any(term in lowered for term in loss_terms):
        inputs_used.append("territory_lost")
        return 0.20, "territory lost or ceded", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine territorial control", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for territorial control determination", tuple(inputs_used)


def _analyze_strategic_waterway(text: str, proposition_status: str) -> tuple[float | None, str, tuple[str, ...]]:
    """Strategic waterway markets: is a waterway open, closed, controlled?

    Look for blockades, closures, opening, control statements."""
    lowered = text.lower()

    blockage_terms = frozenset({
        "blockade", "blockades", "closed", "closure", "shut down",
        "closed to traffic", "blocked", "blocking", "interrupted",
        "disrupted", "cut off", "waterway closed", "navigation closed",
    })
    open_terms = frozenset({
        "open", "opening", "reopened", "navigable", "navigation open",
        "waterway open", "traffic allowed", "ships can pass",
    })

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    if any(term in lowered for term in blockage_terms):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["waterway_blocked", "official_source"])
            return 0.85, "waterway blocked/closed (official source)", tuple(inputs_used)
        inputs_used.append("waterway_blocked")
        return 0.70, "waterway blocked/closed", tuple(inputs_used)

    if any(term in lowered for term in open_terms):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["waterway_open", "official_source"])
            return 0.90, "waterway open (official source)", tuple(inputs_used)
        inputs_used.append("waterway_open")
        return 0.75, "waterway open", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine waterway status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for waterway status determination", tuple(inputs_used)


def _analyze_diplomatic_agreement(text: str, proposition_status: str) -> tuple[float | None, str, tuple[str, ...]]:
    """Diplomatic agreement markets: has a diplomatic agreement been reached?

    Look for signed agreements, announced deals, official statements."""
    lowered = text.lower()

    agreement_terms = frozenset({
        "agreement reached", "deal reached", "agreement signed", "deal signed",
        "agreement announced", "deal announced", "accord reached", "pact reached",
        "agreement approved", "deal approved", "signed agreement", "signed deal",
        "diplomatic agreement", "diplomatic deal", "peace agreement", "peace deal",
    })

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    if any(term in lowered for term in agreement_terms):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["agreement_reached", "official_source"])
            return 0.88, "diplomatic agreement reached (official source)", tuple(inputs_used)
        inputs_used.append("agreement_reached")
        return 0.72, "diplomatic agreement reached", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine agreement status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for diplomatic agreement determination", tuple(inputs_used)


# Mapping from event_type to analysis function
_ANALYSIS_FUNCTIONS: dict[str, callable] = {
    "ceasefire": _analyze_ceasefire,
    "war_escalation": _analyze_war_escalation,
    "military_action": _analyze_military_action,
    "sanctions": _analyze_sanctions,
    "territorial_control": _analyze_territorial_control,
    "strategic_waterway": _analyze_strategic_waterway,
    "diplomatic_agreement": _analyze_diplomatic_agreement,
}


def analyze_geopolitics(
    text: str,
    event_type: str | None,
    proposition_status: str,
    historical_baseline: float | None = None,
) -> GeopoliticsResult:
    """Main entry point: analyze geopolitical proposition and return forecast.

    Args:
        text: The proposition text to analyze (question or resolution text)
        event_type: The geopolitical event type (from semantics.py)
        proposition_status: "CLEAR" or "AMBIGUOUS" from parse_market_proposition
        historical_baseline: Optional historical YES rate from similar cases

    Returns:
        GeopoliticsResult with probability if available, or available=False
        if insufficient structured evidence exists."""
    # Check if this model handles the event type
    if event_type is None or event_type not in _EVENT_TYPES:
        return GeopoliticsResult(
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
            reason=f"event_type '{event_type}' not handled by geopolitical model",
            inputs_used=(),
            contributions=(),
            uncertainty=1.0,
        )

    # Run the appropriate analysis function
    analysis_func = _ANALYSIS_FUNCTIONS[event_type]
    probability, reason, inputs_used = analysis_func(text, proposition_status)

    if probability is None:
        return GeopoliticsResult(
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
            inputs_used=inputs_used,
            contributions=(),
            uncertainty=1.0,
        )

    # Build data quality from inputs
    has_official = "official_source" in inputs_used
    has_direct = any(term in inputs_used for term in ("ceasefire_actual", "escalation_actual", "action_confirmed", "sanctions_imposed", "territory_controlled", "waterway_blocked", "waterway_open", "agreement_reached"))

    data_quality = DataQualityBreakdown(
        vollstaendigkeit=1.0 if has_direct else 0.5,
        aktualitaet=1.0,
        quellenuebereinstimmung=1.0 if has_official else 0.5,
        historische_fallzahl=0.5,
        resolution_klarheit=1.0 if proposition_status == "CLEAR" else 0.5,
        liquiditaet=0.5,
    )

    # Confidence scales with evidence strength
    if has_official and has_direct:
        confidence = 75.0
    elif has_direct:
        confidence = 60.0
    elif has_official:
        confidence = 50.0
    else:
        confidence = 35.0

    # Uncertainty is inverse of confidence (simplified)
    uncertainty = max(0.0, 1.0 - confidence / 100.0)

    # Build contribution breakdown
    contributions: list[dict] = []
    for inp in inputs_used:
        if inp == "official_source":
            contributions.append({"source": "official_statement", "weight": 0.2, "impact": "positive"})
        elif inp == "ceasefire_actual":
            contributions.append({"source": "ceasefire_actual", "weight": 0.3, "impact": "positive"})
        elif inp == "escalation_actual":
            contributions.append({"source": "escalation_actual", "weight": 0.3, "impact": "positive"})
        elif inp == "action_confirmed":
            contributions.append({"source": "action_confirmed", "weight": 0.3, "impact": "positive"})
        elif inp == "sanctions_imposed":
            contributions.append({"source": "sanctions_imposed", "weight": 0.3, "impact": "positive"})
        elif inp == "territory_controlled":
            contributions.append({"source": "territory_controlled", "weight": 0.3, "impact": "positive"})
        elif inp == "waterway_blocked":
            contributions.append({"source": "waterway_blocked", "weight": 0.3, "impact": "positive"})
        elif inp == "waterway_open":
            contributions.append({"source": "waterway_open", "weight": 0.3, "impact": "positive"})
        elif inp == "agreement_reached":
            contributions.append({"source": "agreement_reached", "weight": 0.3, "impact": "positive"})
        elif inp == "ceasefire_denied":
            contributions.append({"source": "ceasefire_denied", "weight": 0.3, "impact": "negative"})
        elif inp == "deescalation_actual":
            contributions.append({"source": "deescalation_actual", "weight": 0.3, "impact": "negative"})
        elif inp == "insufficient_signal":
            contributions.append({"source": "insufficient_signal", "weight": 0.0, "impact": "neutral"})

    return GeopoliticsResult(
        available=True,
        probability=round(probability, 4),
        confidence=confidence,
        data_quality=data_quality,
        reason=reason,
        inputs_used=inputs_used,
        contributions=tuple(contributions),
        uncertainty=uncertainty,
    )


# Backward compatibility alias
def compute_geopolitics_forecast(
    text: str,
    event_type: str | None,
    proposition_status: str,
    historical_baseline: float | None = None,
) -> GeopoliticsResult:
    """Alias for analyze_geopolitics — same interface, preserved for
    backward compatibility during the Phase E transition."""
    return analyze_geopolitics(text, event_type, proposition_status, historical_baseline)
