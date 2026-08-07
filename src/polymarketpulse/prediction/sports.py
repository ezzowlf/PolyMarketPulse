"""Sports Model — forecast POLYMARKET SPORTS markets using structured
data when available, returning unavailable otherwise.

Supports only cases for which real structured data is available.

At minimum distinguishes:
  - match winner
  - tournament winner
  - qualification

Potential features (when real data exists):
  - team/player strength
  - standings
  - bracket
  - recent results
  - remaining opponents
  - home/away

Critical constraints:
  - This is for POLYMARKET SPORTS only — NO Tipico or other bookmakers
  - Do NOT generate sports probabilities from news sentiment
  - If no reliable structured sports data exists: return unavailable

Design principle:
  - Only use actual match results/standings data
  - If data unavailable: available=False with clear reason"""

from __future__ import annotations

from dataclasses import dataclass

from .types import DataQualityBreakdown

# Event types this model handles
_EVENT_TYPES = frozenset({
    "sport_match", "sport_tournament", "sport_qualification",
    "sport_winner", "sport_final",
})

# Supported sports (for structure parsing)
_SUPPORTED_SPORTS = frozenset({
    "soccer", "football", "tennis", "basketball", "baseball",
    "hockey", "volleyball", "esports", "league of legends",
    "lol", "counter-strike", "cs", "csgo",
})

# Match result indicators (for actual match markets)
_MATCH_RESULT_KEYWORDS = frozenset({
    "won", "wins", "defeated", "beat", "triumph", "victory",
    "lost", "loss", "defeat", "failed", "lost the match",
    "match won", "match lost", "game won", "game lost",
})

# Tournament outcome indicators
_TOURNAMENT_RESULT_KEYWORDS = frozenset({
    "champion", "winner", "won the tournament", "took the title",
    "defeated in final", "lost in final", "runner-up",
    "champions", "winners", "championship",
})

# Qualification indicators
_QUALIFICATION_RESULT_KEYWORDS = frozenset({
    "qualified", "advance", "progress", "seeds", "makes it",
    "fails to qualify", "does not qualify", "eliminated",
    "knocked out", "out of tournament", "did not advance",
})


@dataclass(frozen=True)
class SportsResult:
    """Result of the sports forecast model."""

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


def _analyze_match_winner(
    text: str,
    proposition_status: str,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Match winner analysis — has a specific team/player won?

    Key: look for actual match result, not schedule or prediction."""
    lowered = text.lower()

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    # Check for match result language
    if any(kw in lowered for kw in _MATCH_RESULT_KEYWORDS):
        # This indicates the match has already happened
        inputs_used.append("match_result_detected")
        # Without knowing the specific team mentioned in the market
        # vs. the actual winner, we cannot assign a probability
        reason_parts.append("match result detected but specific winner not identified")
        return None, "Match result detected but insufficient detail to assign probability", tuple(inputs_used)

    # Check for schedule/future language (no decision yet)
    schedule_keywords = frozenset({
        "vs", "versus", "vs.", "playing", "game today", "match today",
        "upcoming match", "scheduled match", "set to play", "going to play",
        "match schedule", "matchup", "face off", "battle",
    })

    if any(kw in lowered for kw in schedule_keywords):
        inputs_used.append("future_match")
        reason_parts.append("match scheduled but not yet played")
        return None, "Match scheduled but not yet played — no outcome yet", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "Proposition ambiguous — could not determine match status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "Insufficient structured evidence for match determination", tuple(inputs_used)


def _analyze_tournament_winner(
    text: str,
    proposition_status: str,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Tournament winner analysis — who wins the tournament?

    Key: look for actual champion announcement or bracket progression."""
    lowered = text.lower()

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    # Check for tournament result language
    if any(kw in lowered for kw in _TOURNAMENT_RESULT_KEYWORDS):
        inputs_used.append("tournament_result_detected")
        return None, "Tournament result detected but specific winner not identified", tuple(inputs_used)

    # Check for tournament schedule (not yet played)
    schedule_keywords = frozenset({
        "tournament starting", "tournament begins", "upcoming tournament",
        "scheduled tournament", "tournament today", "playoffs starting",
        "championship starting", "bracket release", "first round",
    })

    if any(kw in lowered for kw in schedule_keywords):
        inputs_used.append("future_tournament")
        reason_parts.append("tournament scheduled but not yet played")
        return None, "Tournament scheduled but not yet played — no outcome yet", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "Proposition ambiguous — could not determine tournament status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "Insufficient structured evidence for tournament determination", tuple(inputs_used)


def _analyze_qualification(
    text: str,
    proposition_status: str,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Qualification analysis — does a team/player qualify?

    Key: look for actual qualification outcome."""
    lowered = text.lower()

    inputs_used: list[str] = []
    reason_parts: list[str] = []

    # Check for qualification language
    if any(kw in lowered for kw in _QUALIFICATION_RESULT_KEYWORDS):
        inputs_used.append("qualification_result_detected")
        return None, "Qualification result detected but specific outcome not identified", tuple(inputs_used)

    # Check for qualification schedule
    schedule_keywords = frozenset({
        "qualifying round", "qualification match", "play-in game",
        "first round", "qualifying starts", "playoffs begin",
    })

    if any(kw in lowered for kw in schedule_keywords):
        inputs_used.append("future_qualification")
        reason_parts.append("Qualification round scheduled but not yet played")
        return None, "Qualification round scheduled but not yet played — no outcome yet", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "Proposition ambiguous — could not determine qualification status", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "Insufficient structured evidence for qualification determination", tuple(inputs_used)


# Mapping from event_type to analysis function
_ANALYSIS_FUNCTIONS: dict[str, callable] = {
    "sport_match": _analyze_match_winner,
    "sport_tournament": _analyze_tournament_winner,
    "sport_qualification": _analyze_qualification,
    "sport_winner": _analyze_tournament_winner,
    "sport_final": _analyze_tournament_winner,
}


def analyze_sports(
    text: str,
    event_type: str | None,
    proposition_status: str,
    sport: str | None = None,
    team1: str | None = None,
    team2: str | None = None,
) -> SportsResult:
    """Main entry point: analyze sports proposition.

    Args:
        text: The proposition text to analyze
        event_type: The sports event type
        proposition_status: "CLEAR" or "AMBIGUOUS"
        sport: The sport being played (optional, for context)
        team1: First team/player (optional)
        team2: Second team/player (optional)

    Returns:
        SportsResult with probability if available, or available=False."""
    inputs_used: list[str] = []

    # Check if this model handles the event type
    if event_type is None or event_type not in _EVENT_TYPES:
        return SportsResult(
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
            reason=f"event_type '{event_type}' not handled by sports model",
            inputs_used=(),
            contributions=(),
            uncertainty=1.0,
        )

    # Check if sport is supported (for context)
    if sport is not None:
        sport_lower = sport.lower()
        if not any(s in sport_lower for s in _SUPPORTED_SPORTS):
            inputs_used.append("unsupported_sport")
            # Still try to analyze based on proposition text alone
            # but note the unsupported sport

    # Run the appropriate analysis function
    analysis_func = _ANALYSIS_FUNCTIONS.get(event_type, _analyze_match_winner)
    probability, reason, estimation_inputs = analysis_func(text, proposition_status, event_type)

    inputs_used.extend(estimation_inputs)

    if probability is None:
        return SportsResult(
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
    data_quality = DataQualityBreakdown(
        vollstaendigkeit=1.0 if "result_detected" in inputs_used else 0.3,
        aktualitaet=0.5,
        quellenuebereinstimmung=0.3,
        historische_fallzahl=0.3,
        resolution_klarheit=1.0 if proposition_status == "CLEAR" else 0.5,
        liquiditaet=0.3,
    )

    # Confidence based on result clarity
    if "result_detected" in inputs_used:
        confidence = 30.0  # We know a result exists but not the specifics
    else:
        confidence = 10.0  # No result data at all

    uncertainty = max(0.0, 1.0 - confidence / 100.0)

    # Build contribution breakdown
    contributions: list[dict] = []
    for inp in inputs_used:
        if inp == "match_result_detected":
            contributions.append({"source": "match_result", "weight": 0.2, "impact": "neutral"})
        elif inp == "tournament_result_detected":
            contributions.append({"source": "tournament_result", "weight": 0.2, "impact": "neutral"})
        elif inp == "qualification_result_detected":
            contributions.append({"source": "qualification_result", "weight": 0.2, "impact": "neutral"})
        elif inp == "future_match":
            contributions.append({"source": "future_match", "weight": 0.0, "impact": "neutral"})
        elif inp == "future_tournament":
            contributions.append({"source": "future_tournament", "weight": 0.0, "impact": "neutral"})
        elif inp == "future_qualification":
            contributions.append({"source": "future_qualification", "weight": 0.0, "impact": "neutral"})
        elif inp == "insufficient_signal":
            contributions.append({"source": "insufficient_signal", "weight": 0.0, "impact": "neutral"})

    return SportsResult(
        available=True,
        probability=round(probability, 4) if probability is not None else None,
        confidence=confidence,
        data_quality=data_quality,
        reason=reason,
        inputs_used=tuple(inputs_used),
        contributions=tuple(contributions),
        uncertainty=uncertainty,
    )


# Backward compatibility alias
def compute_sports_forecast(
    text: str,
    event_type: str | None,
    proposition_status: str,
    sport: str | None = None,
    team1: str | None = None,
    team2: str | None = None,
) -> SportsResult:
    """Alias for analyze_sports — same interface, preserved for
    backward compatibility during the Phase E transition."""
    return analyze_sports(text, event_type, proposition_status, sport, team1, team2)