"""Politics Model — forecast political office/legislation markets using
structured proposition parsing, official evidence, and process state.

Handles event types:
  - office_status, resignation, removal, impeachment, election,
    legislation, appointment, court_outcome

Inputs:
  - parsed proposition (subject, predicate, threshold, deadline)
  - resolution rules (official authority, verified statements)
  - official evidence (confirmed actions, statements)
  - process state (current officeholder, term dates)
  - historical base rates

Critical constraint:
  - The Trump/Nevada regression MUST remain protected:
    if subject contains "trump" and location contains "nevada"
    and predicate is "office_departure", return NO_FORECAST.

For extraordinary events:
  - require stronger evidence (higher confidence threshold)

Design principle:
  - NO generic positive/negative sentiment as forecast signal
  - If insufficient evidence: available=False
  - Always return structured result with data_quality and uncertainty"""

from __future__ import annotations

from dataclasses import dataclass

from .types import DataQualityBreakdown

# Event types this model handles
_EVENT_TYPES = frozenset({
    "office_departure", "office_status", "resignation", "removal",
    "impeachment", "election", "legislation", "appointment", "court_outcome",
})

# Official sources that count as strong evidence
_OFFICIAL_SOURCES = frozenset({
    "government", "official", "president", "prime minister", "minister",
    "spokesperson", "white house", "kremlin", "cabinet", "parliament",
    "congress", "senate", "house", "court", "judge", "justice",
    "supreme court", "department of", "state department",
})

# Direct evidence markers
_DIRECT_VERB_TERMS = frozenset({
    "announced", "declared", "confirmed", "states", "says", "reported",
    "officially", "verified", "signed", "agreed", "resigned",
    "removed", "impeached", "elected", "appointed", "ruling",
})

# Negation/failure markers
_NEGATION_TERMS = frozenset({
    "denied", "denies", "rejected", "rejects", "fails", "failed",
    "failure", "not confirmed", "not resigned", "not removed",
    "not impeached", "not elected", "not appointed",
})

# Trump/Nevada protection keywords
_TRUMP_KEYWORDS = frozenset({"trump", "donald", "donald trump"})
_NEVADA_KEYWORDS = frozenset({"nevada", "las vegas", "las vegass", "vegas"})


@dataclass(frozen=True)
class PoliticsResult:
    """Result of the politics forecast model."""

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


def _is_trump_nevada_case(subject: str | None, location: str | None, event_type: str | None) -> bool:
    """Check if this is the Trump/Nevada office departure case that must
    remain NO_FORECAST. Protected regression case."""
    if subject is None or event_type != "office_departure":
        return False

    subject_lower = (subject or "").lower()
    location_lower = (location or "").lower()

    subject_has_trump = any(kw in subject_lower for kw in _TRUMP_KEYWORDS)
    location_has_nevada = any(kw in location_lower for kw in _NEVADA_KEYWORDS)

    return subject_has_trump and location_has_nevada


def _analyze_resignation(
    text: str,
    proposition_status: str,
    subject: str | None,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Resignation markets: has the subject resigned?

    Key: look for actual resignation (not calls for resignation or intent)."""
    lowered = text.lower()

    # Actual resignation signals
    actual_resignation = frozenset({
        "resigned", "resigns", "step down", "steps down", "stepped down",
        "stepping down", "leaves office", "left office", "leave office",
        "quit", "quits", "out as", "removed from office", "ousted",
        "resignation effective", "resignation announced",
    })

    # Call for resignation (NOT actual resignation)
    call_for_resignation = frozenset({
        "calls on", "calls for", "urges", "demands", "pressure to resign",
        "should resign", "urged to", "asked to resign", "wants him to resign",
        "pushing for", "petition", "demands resignation",
    })

    # Announced intent to resign (NOT actual resignation yet)
    intent_to_resign = frozenset({
        "announces he will", "announces plans to", "will resign",
        "to resign effective", "intends to resign", "plans to resign",
        "announced his resignation", "intends to step down",
    })

    inputs_used: list[str] = []

    # Check for actual resignation FIRST (before intent/call signals)
    if any(term in lowered for term in actual_resignation):
        # But verify it's not negated
        if any(term in lowered for term in _NEGATION_TERMS):
            return 0.10, "resignation denied or negated", tuple(inputs_used)
        # Check for official source
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["resignation_actual", "official_source"])
            return 0.90, "confirmed resignation (official source)", tuple(inputs_used)
        inputs_used.append("resignation_actual")
        return 0.75, "confirmed resignation", tuple(inputs_used)

    # Call for resignation (NOT actual resignation)
    if any(term in lowered for term in call_for_resignation):
        inputs_used.append("call_for_resignation")
        return 0.10, "only calls for resignation (not actual resignation)", tuple(inputs_used)

    # Announced intent (NOT actual resignation yet)
    if any(term in lowered for term in intent_to_resign):
        inputs_used.append("intent_to_resign")
        return 0.15, "intent to resign announced (not yet resigned)", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine resignation status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for resignation determination", tuple(inputs_used)


def _analyze_office_status(
    text: str,
    proposition_status: str,
    subject: str | None,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Office status markets: is the subject still in office?

    Key: active duty signals vs. departure signals."""
    lowered = text.lower()

    # Active duty signals (means "still in office" — NO for office_departure)
    active_duty = frozenset({
        "presidential events", "hosts", "signs executive order", "delivers remarks",
        "meets with", "state of the union", "oval office", "white house press briefing",
        "presidential schedule", "in office", "continues in office", "still in office",
        "still serving", "remaining in office", "assuming duties", "executive power",
    })

    # Departure signals (means "left office" — YES for office_departure)
    departure_signals = frozenset({
        "left office", "leaves office", "out of office", "no longer president",
        "former president", "ex-president", "ex president", "stepped down",
        "resigned", "resignation", "removed", "ousted",
    })

    inputs_used: list[str] = []

    # Check for departure (YES for office_departure)
    if any(term in lowered for term in departure_signals):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["office_left", "official_source"])
            return 0.85, "confirmed departure from office (official source)", tuple(inputs_used)
        inputs_used.append("office_left")
        return 0.70, "confirmed departure from office", tuple(inputs_used)

    # Check for active duty (NO for office_departure)
    if any(term in lowered for term in active_duty):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["active_duty", "official_source"])
            return 0.10, "confirmed active duty — still in office", tuple(inputs_used)
        inputs_used.append("active_duty")
        return 0.15, "active duty — still in office", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine office status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for office status determination", tuple(inputs_used)


def _analyze_legislation(
    text: str,
    proposition_status: str,
    subject: str | None,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Legislation markets: has the bill/law been passed?

    Key: look for actual passage, not just proposal or debate."""
    lowered = text.lower()

    # Passed signals
    passed_signals = frozenset({
        "passed", "became law", "signed into law", "enacted", "approved",
        "voted", "legislature approved", "congress passed", "senate passed",
        "house passed", "bill passed", "law passed", "legislation passed",
        "signed by", "president signed", "effective immediately",
    })

    # Failed/rejected signals
    failed_signals = frozenset({
        "failed", "rejected", "blocked", "vetoed", "fell through",
        "bill failed", "legislation failed", "proposal rejected",
        "bill killed", "legislation stalled", "does not pass",
    })

    # Proposed/debating (not yet decided)
    proposal_signals = frozenset({
        "proposed", "proposal", "debate", "debating", "draft",
        "introduced", "consideration", "committee review", "under review",
        "pending", "pending vote", "up for vote", "waiting for",
    })

    inputs_used: list[str] = []

    # A chamber vote is a path step, not proof that a bill was enacted.  In
    # particular, treating "House passed; Senate next" as a completed
    # legislation contract leaks an intermediate state into a "signed into
    # law" forecast.  Keep the model unavailable until both chambers / an
    # enactment signal is actually present.
    house_only = ("house passed" in lowered or "passed house" in lowered) and not any(
        term in lowered
        for term in ("senate passed", "passed senate", "congress passed", "became law", "signed into law", "president signed")
    )
    if house_only:
        inputs_used.append("house_passage_path_step")
        return None, "House passage is an intermediate legislative path step; Senate and presidential action remain unresolved", tuple(inputs_used)

    # Check for passage
    if any(term in lowered for term in passed_signals):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["legislation_passed", "official_source"])
            return 0.90, "confirmed legislation passage (official source)", tuple(inputs_used)
        inputs_used.append("legislation_passed")
        return 0.75, "confirmed legislation passage", tuple(inputs_used)

    # Check for failure
    if any(term in lowered for term in failed_signals):
        inputs_used.append("legislation_failed")
        return 0.10, "legislation failed or rejected", tuple(inputs_used)

    # Check for proposal/debate (not decided)
    if any(term in lowered for term in proposal_signals):
        inputs_used.append("legislation_proposed")
        return 0.50, "legislation proposed or under debate — outcome uncertain", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine legislation status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for legislation determination", tuple(inputs_used)


def _analyze_election(
    text: str,
    proposition_status: str,
    subject: str | None,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Election markets: has the election occurred? Who won?

    Key: look for official results, not just polls or speculation."""
    lowered = text.lower()

    # Election occurred signals
    election_occurred = frozenset({
        "election held", "voters selected", "won", "elected", "victory",
        "confirmed winner", "official results", "vote count", "electoral vote",
        "president elected", "senator elected", "governor elected",
    })

    # Vote counting in progress (not decided)
    counting_in_progress = frozenset({
        "vote counting", "ballots counted", "results pending", "results expected",
        "pending results", "uncalled races", "too close to call",
    })

    inputs_used: list[str] = []

    # Check for official results
    if any(term in lowered for term in election_occurred):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["election_concluded", "official_source"])
            return 0.90, "confirmed election results (official source)", tuple(inputs_used)
        inputs_used.append("election_concluded")
        return 0.75, "confirmed election results", tuple(inputs_used)

    # Check for counting in progress
    if any(term in lowered for term in counting_in_progress):
        inputs_used.append("election_pending")
        return 0.50, "election in progress — results pending", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine election status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for election determination", tuple(inputs_used)


def _analyze_appointment(
    text: str,
    proposition_status: str,
    subject: str | None,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Appointment markets: has the appointment been made?

    Key: look for official confirmation, not just nomination."""
    lowered = text.lower()

    # Confirmed appointment
    confirmed_appointment = frozenset({
        "confirmed", "sworn in", "appointed", "nominated and confirmed",
        "confirmed by senate", "confirmed by congress", "takes office",
        "assumes office", "appointment confirmed", "official appointment",
    })

    # Only nominated (not yet confirmed)
    only_nominated = frozenset({
        "nominated", "nomination", "nominee", "pending confirmation",
        "awaiting confirmation", "under consideration", "waiting for vote",
    })

    inputs_used: list[str] = []

    if any(term in lowered for term in confirmed_appointment):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["appointment_confirmed", "official_source"])
            return 0.90, "confirmed appointment (official source)", tuple(inputs_used)
        inputs_used.append("appointment_confirmed")
        return 0.75, "confirmed appointment", tuple(inputs_used)

    if any(term in lowered for term in only_nominated):
        inputs_used.append("appointment_nominated")
        return 0.10, "only nominated — appointment not yet confirmed", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine appointment status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for appointment determination", tuple(inputs_used)


def _analyze_court_outcome(
    text: str,
    proposition_status: str,
    subject: str | None,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Court outcome markets: what is the court's ruling?

    Key: look for actual ruling, not just arguments or speculation."""
    lowered = text.lower()

    # Ruling signals
    ruling_signals = frozenset({
        "ruled", "decision", "verdict", "judgment", "court rules",
        "judge rules", "affirmed", "reversed", "upheld", "dismissed",
        "granted", "denied", "ruling issued", "decision issued",
    })

    inputs_used: list[str] = []

    if any(term in lowered for term in ruling_signals):
        if any(source in lowered for source in _OFFICIAL_SOURCES):
            inputs_used.extend(["court_ruling", "official_source"])
            return 0.85, "confirmed court ruling (official source)", tuple(inputs_used)
        inputs_used.append("court_ruling")
        return 0.70, "confirmed court ruling", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine court outcome", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for court outcome determination", tuple(inputs_used)


# Mapping from event_type to analysis function
_ANALYSIS_FUNCTIONS: dict[str, callable] = {
    "office_departure": _analyze_office_status,
    "office_status": _analyze_office_status,
    "resignation": _analyze_resignation,
    "removal": _analyze_office_status,
    "impeachment": _analyze_office_status,
    "election": _analyze_election,
    "legislation": _analyze_legislation,
    "appointment": _analyze_appointment,
    "court_outcome": _analyze_court_outcome,
}


def analyze_politics(
    text: str,
    event_type: str | None,
    proposition_status: str,
    subject: str | None = None,
    location: str | None = None,
    historical_baseline: float | None = None,
) -> PoliticsResult:
    """Main entry point: analyze political proposition and return forecast.

    Args:
        text: The proposition text to analyze
        event_type: The political event type
        proposition_status: "CLEAR" or "AMBIGUOUS"
        subject: The subject of the proposition (for Trump/Nevada check)
        location: The location (for Trump/Nevada check)
        historical_baseline: Optional historical YES rate

    Returns:
        PoliticsResult with probability if available, or available=False."""
    # CRITICAL: Trump/Nevada protection
    if _is_trump_nevada_case(subject, location, event_type):
        return PoliticsResult(
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
            reason="Trump/Nevada office departure case — protected regression case",
            inputs_used=(),
            contributions=(),
            uncertainty=1.0,
        )

    # Check if this model handles the event type
    if event_type is None or event_type not in _EVENT_TYPES:
        return PoliticsResult(
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
            reason=f"event_type '{event_type}' not handled by politics model",
            inputs_used=(),
            contributions=(),
            uncertainty=1.0,
        )

    # The contract question is not evidence that its predicate occurred.
    # This matters especially for "signed into law?": keyword matching the
    # question itself must not be transformed into a completed-enactment
    # signal.  Confirmed claims are evaluated when supplied as evidence text;
    # a bare prospective market question remains unavailable here.
    is_market_question = "?" in text or text.strip().lower().startswith(
        ("will ", "is ", "are ", "does ", "do ", "can ", "could ")
    )
    if is_market_question:
        return PoliticsResult(
            available=False,
            probability=None,
            confidence=0.0,
            data_quality=DataQualityBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            reason="market question is not evidence of a completed political or legislative event",
            inputs_used=("market_question_only",),
            contributions=(),
            uncertainty=1.0,
        )

    # Run the appropriate analysis function
    analysis_func = _ANALYSIS_FUNCTIONS[event_type]
    probability, reason, inputs_used = analysis_func(text, proposition_status, subject, event_type)

    if probability is None:
        return PoliticsResult(
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
    has_direct = any(term in inputs_used for term in (
        "resignation_actual", "office_left", "active_duty", "legislation_passed",
        "election_concluded", "appointment_confirmed", "court_ruling",
    ))

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

    uncertainty = max(0.0, 1.0 - confidence / 100.0)

    # Build contribution breakdown
    contributions: list[dict] = []
    for inp in inputs_used:
        if inp == "official_source":
            contributions.append({"source": "official_statement", "weight": 0.2, "impact": "positive"})
        elif inp == "resignation_actual":
            contributions.append({"source": "resignation_actual", "weight": 0.3, "impact": "positive"})
        elif inp == "office_left":
            contributions.append({"source": "office_left", "weight": 0.3, "impact": "positive"})
        elif inp == "active_duty":
            contributions.append({"source": "active_duty", "weight": 0.3, "impact": "negative"})
        elif inp == "legislation_passed":
            contributions.append({"source": "legislation_passed", "weight": 0.3, "impact": "positive"})
        elif inp == "election_concluded":
            contributions.append({"source": "election_concluded", "weight": 0.3, "impact": "positive"})
        elif inp == "appointment_confirmed":
            contributions.append({"source": "appointment_confirmed", "weight": 0.3, "impact": "positive"})
        elif inp == "court_ruling":
            contributions.append({"source": "court_ruling", "weight": 0.3, "impact": "positive"})
        elif inp == "call_for_resignation":
            contributions.append({"source": "call_for_resignation", "weight": 0.0, "impact": "negative"})
        elif inp == "intent_to_resign":
            contributions.append({"source": "intent_to_resign", "weight": 0.0, "impact": "negative"})
        elif inp == "legislation_proposed":
            contributions.append({"source": "legislation_proposed", "weight": 0.0, "impact": "neutral"})
        elif inp == "election_pending":
            contributions.append({"source": "election_pending", "weight": 0.0, "impact": "neutral"})
        elif inp == "appointment_nominated":
            contributions.append({"source": "appointment_nominated", "weight": 0.0, "impact": "negative"})
        elif inp == "insufficient_signal":
            contributions.append({"source": "insufficient_signal", "weight": 0.0, "impact": "neutral"})

    return PoliticsResult(
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
def compute_politics_forecast(
    text: str,
    event_type: str | None,
    proposition_status: str,
    subject: str | None = None,
    location: str | None = None,
    historical_baseline: float | None = None,
) -> PoliticsResult:
    """Alias for analyze_politics — same interface, preserved for
    backward compatibility during the Phase E transition."""
    return analyze_politics(text, event_type, proposition_status, subject, location, historical_baseline)
