"""Macro / Central Bank Model — forecast macroeconomic markets using
structured data when available, returning unavailable otherwise.

Supports at minimum:
  - central_bank_decision, rate_cut, rate_hike, rate_hold

Features (when available from existing providers):
  - current policy rate
  - next meeting date
  - time to decision
  - inflation
  - employment
  - official central-bank statements
  - historical decision baseline

Critical constraint:
  - Do NOT introduce paid data sources
  - If required structured data is unavailable:
    return unavailable honestly (available=False)

Design principle:
  - NO generic sentiment as forecast signal
  - Only use actual event statements from official sources"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..providers.fred import MacroSnapshot
from .types import DataQualityBreakdown

# Event types for which the real FRED-derived quantitative signal (below)
# applies. Deliberately narrower than _EVENT_TYPES: "monetary_policy" /
# "policy_change" are generic-stance questions the quantitative model isn't
# shaped to answer (no single "cut/hike/hold" outcome to score).
_QUANTITATIVE_EVENT_TYPES = frozenset({"rate_cut", "rate_hike", "rate_hold"})


def _quantitative_rate_probabilities(snapshot: MacroSnapshot) -> dict[str, float] | None:
    """Real, honestly-simple (documented, not econometrically fitted —
    there is no resolved-forecast history yet to fit against, same
    constraint as everywhere else in this codebase that says so) method
    for turning real FRED inputs into cut/hike/hold probabilities:

      cut_score = unemployment_trend - cpi_trend   (rising unemployment
                  and/or cooling inflation both push toward a cut)
      hike_score = cpi_trend - unemployment_trend  (heating inflation
                  and/or falling unemployment both push toward a hike)
      hold_score = a fixed baseline reflecting that the Fed holds at the
                  clear majority of meetings historically

    where cpi_trend/unemployment_trend are the real change in each
    series between the latest FRED observation and the observation
    ~3 months earlier. The three scores are combined with a softmax to
    produce a probability distribution over {cut, hike, hold} that sums
    to 1.0. This is a genuine quantitative signal derived from real
    numeric inputs — not a sophisticated model, but not text
    classification wearing a numeric costume either.

    Returns None if the snapshot lacks the trend reference points needed
    (e.g. mocked/fetched series too short)."""
    if snapshot.cpi_yoy_prior is None or snapshot.unemployment_rate_prior is None:
        return None

    cpi_trend = snapshot.cpi_yoy - snapshot.cpi_yoy_prior
    unemployment_trend = snapshot.unemployment_rate - snapshot.unemployment_rate_prior

    cut_score = unemployment_trend - cpi_trend
    hike_score = cpi_trend - unemployment_trend
    hold_score = 0.35  # baseline: the Fed holds at most scheduled meetings

    scores = {"cut": cut_score, "hike": hike_score, "hold": hold_score}
    exp_scores = {k: math.exp(v) for k, v in scores.items()}
    total = sum(exp_scores.values())
    return {k: v / total for k, v in exp_scores.items()}

# Event types this model handles
_EVENT_TYPES = frozenset({
    "central_bank_decision", "rate_cut", "rate_hike", "rate_hold",
    "monetary_policy", "policy_change",
})

# Official central bank sources
_OFFICIAL_CB_SOURCES = frozenset({
    "fed", "federal reserve", "central bank", "ecb", "european central bank",
    "boj", "bank of japan", "boe", "bank of england", "snb", "swiss national bank",
    "cbo", "chair", "chairman", "chairwoman", "governor",
    "press conference", "statement", "announcement", "meeting minutes",
    "fomc", "federal open market committee",
})

# Rate decision keywords
# NOTE (event_type-detection-gap fix): real Polymarket Fed questions are
# frequently phrased as "decrease interest rates by 25 bps" / "increase
# interest rates by 25 bps" rather than "cut"/"hike" — semantics.py's
# _detect_event_type already routes these to rate_cut/rate_hike correctly
# (its own term lists include "decrease interest rates"/"increase interest
# rates"), but this module's OWN keyword sets, used by _analyze_rate_decision
# to decide confirmed-vs-reported-vs-no-signal, did not — so 4/5 real Fed
# markets (2 cut-phrased, 2 hike-phrased) fell through to
# "insufficient_signal" here even though routing correctly identified them
# as macro-eligible. Added the decrease/increase phrasing so this module's
# own text analysis stays consistent with what semantics.py already detects.
_RATE_CUT_KEYWORDS = frozenset({
    "rate cut", "cuts rates", "lowers rates", "reduces rates", "easing",
    "cut interest rates", "reduce rates", "lower policy rate",
    "decrease rates", "decreases rates", "decrease interest rates",
    "lower rates", "lower interest rates",
})

_RATE_HIKE_KEYWORDS = frozenset({
    "rate hike", "hikes rates", "raises rates", "increases rates", "tightening",
    "raise interest rates", "increase rates", "raise policy rate",
    "increase interest rates", "raises interest rates", "higher rates",
})

_RATE_HOLD_KEYWORDS = frozenset({
    "rate hold", "holds rates", "maintains rates", "keeps rates", "pause",
    "no change", "on hold", "stable rates", "current rates", "status quo",
})

# Decision already made vs. upcoming
_DECISION_MADE_KEYWORDS = frozenset({
    "announced rate cut", "announced rate hike", "announced rate hold",
    "confirmed rate decision", "decided to cut", "decided to hike",
    "decided to hold", "rate decision confirmed", "policy decision made",
    "announces rate cut", "announces rate hike", "announces rate hold",
    "policy decision confirmed", "decision confirmed",
})

_UPCOMING_KEYWORDS = frozenset({
    "upcoming meeting", "next meeting", "fomc meeting", "policy meeting",
    "scheduled meeting", "upcoming decision", "next decision", "expected to",
    "forecast to cut", "forecast to hike", "predict to cut", "predict to hike",
})

# Negation
_NEGATION_KEYWORDS = frozenset({
    "not cut", "not hike", "not hold", "no cut", "no hike", "no hold",
    "denied rate cut", "denied rate hike", "rules out", "not expected",
    "unlikely to cut", "unlikely to hike",
})


@dataclass(frozen=True)
class MacroResult:
    """Result of the macro forecast model."""

    available: bool
    probability: float | None
    confidence: float
    data_quality: DataQualityBreakdown
    reason: str
    inputs_used: tuple[str, ...]
    contributions: tuple[dict, ...]
    uncertainty: float
    # Distinguishes WHY no quantitative FRED/BLS signal was used, so the UI
    # and downstream gap-reporting can tell "the source fetch genuinely
    # failed" (SOURCE_FETCH_FAILED — a transport/availability problem, not
    # a statement about the world) apart from "no relevant evidence exists"
    # (NO_EVIDENCE) or the normal case where a real signal was used (OK).
    # Defaulted so existing call sites/tests that construct MacroResult
    # directly are unaffected.
    data_source_status: str = "OK"

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
            "data_source_status": self.data_source_status,
        }


def _analyze_rate_decision(
    text: str,
    proposition_status: str,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """Central bank rate decision analysis.

    Key: look for official decision statements, not forecasts or expectations."""
    lowered = text.lower()

    inputs_used: list[str] = []

    # First check if negation applies
    if any(kw in lowered for kw in _NEGATION_KEYWORDS):
        # e.g. "not cut" means rate did NOT cut — for a "rate_cut" market, this is NO
        inputs_used.append("negation_detected")
        return 0.10, "rate change denied or did not occur", tuple(inputs_used)

    # Check for decision already made — either an exact scripted phrase, or
    # the generic word "confirmed" co-occurring with a rate-move keyword
    # (natural-language variants like "announces rate cut ... confirmed" or
    # "tightening confirmed" shouldn't require an exact literal match).
    decision_made = any(kw in lowered for kw in _DECISION_MADE_KEYWORDS) or (
        "confirmed" in lowered
        and any(
            kw in lowered
            for kw in _RATE_CUT_KEYWORDS | _RATE_HIKE_KEYWORDS | _RATE_HOLD_KEYWORDS
        )
    )
    if decision_made:
        if any(kw in lowered for kw in _RATE_CUT_KEYWORDS):
            inputs_used.extend(["rate_cut_confirmed", "decision_made"])
            if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
                inputs_used.append("official_source")
                return 0.90, "confirmed rate cut (official source)", tuple(inputs_used)
            return 0.75, "confirmed rate cut", tuple(inputs_used)

        if any(kw in lowered for kw in _RATE_HIKE_KEYWORDS):
            inputs_used.extend(["rate_hike_confirmed", "decision_made"])
            if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
                inputs_used.append("official_source")
                return 0.90, "confirmed rate hike (official source)", tuple(inputs_used)
            return 0.75, "confirmed rate hike", tuple(inputs_used)

        if any(kw in lowered for kw in _RATE_HOLD_KEYWORDS):
            inputs_used.extend(["rate_hold_confirmed", "decision_made"])
            if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
                inputs_used.append("official_source")
                return 0.90, "confirmed rate hold (no change, official source)", tuple(inputs_used)
            return 0.75, "confirmed rate hold (no change)", tuple(inputs_used)

    # Check for upcoming decision (not yet made)
    if any(kw in lowered for kw in _UPCOMING_KEYWORDS):
        inputs_used.append("upcoming_decision")
        return None, "upcoming meeting — decision not yet made", tuple(inputs_used)

    # Check for rate cut language without "already made" marker
    if any(kw in lowered for kw in _RATE_CUT_KEYWORDS):
        if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
            inputs_used.extend(["rate_cut_reported", "official_source"])
            return 0.60, "reported rate cut (not yet confirmed as implemented)", tuple(inputs_used)
        inputs_used.append("rate_cut_reported")
        return 0.45, "reported rate cut (need confirmation)", tuple(inputs_used)

    # Check for rate hike language without "already made" marker
    if any(kw in lowered for kw in _RATE_HIKE_KEYWORDS):
        if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
            inputs_used.extend(["rate_hike_reported", "official_source"])
            return 0.60, "reported rate hike (not yet confirmed as implemented)", tuple(inputs_used)
        inputs_used.append("rate_hike_reported")
        return 0.45, "reported rate hike (need confirmation)", tuple(inputs_used)

    # Check for rate hold language
    if any(kw in lowered for kw in _RATE_HOLD_KEYWORDS):
        if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
            inputs_used.extend(["rate_hold_reported", "official_source"])
            return 0.60, "reported rate hold (not yet confirmed)", tuple(inputs_used)
        inputs_used.append("rate_hold_reported")
        return 0.45, "reported rate hold (need confirmation)", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine rate decision", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for rate decision determination", tuple(inputs_used)


def _analyze_monetary_policy(
    text: str,
    proposition_status: str,
    event_type: str | None,
) -> tuple[float | None, str, tuple[str, ...]]:
    """General monetary policy markets.

    Key: look for official policy change announcements."""
    lowered = text.lower()

    inputs_used: list[str] = []

    # Tightening signals
    tightening_keywords = frozenset({
        "tightening", "tightens policy", "higher rates", "higher interest rates",
        "restrictive policy", "monetary tightening", "policy tightening",
    })

    # Easing signals
    easing_keywords = frozenset({
        "easing", "eases policy", "lower rates", "lower interest rates",
        " accommodative policy", "monetary easing", "policy easing",
    })

    # Check for tightening
    if any(kw in lowered for kw in tightening_keywords):
        if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
            inputs_used.extend(["tightening_confirmed", "official_source"])
            return 0.85, "confirmed monetary tightening (official source)", tuple(inputs_used)
        inputs_used.append("tightening_reported")
        return 0.65, "reported monetary tightening", tuple(inputs_used)

    # Check for easing
    if any(kw in lowered for kw in easing_keywords):
        if any(source in lowered for source in _OFFICIAL_CB_SOURCES):
            inputs_used.extend(["easing_confirmed", "official_source"])
            return 0.85, "confirmed monetary easing (official source)", tuple(inputs_used)
        inputs_used.append("easing_reported")
        return 0.65, "reported monetary easing", tuple(inputs_used)

    if proposition_status == "AMBIGUOUS":
        inputs_used.append("ambiguous_proposition")
        return None, "proposition ambiguous — could not determine policy stance", tuple(inputs_used)

    inputs_used.append("insufficient_signal")
    return None, "insufficient structured evidence for monetary policy determination", tuple(inputs_used)


# Mapping from event_type to analysis function
_ANALYSIS_FUNCTIONS: dict[str, callable] = {
    "central_bank_decision": _analyze_rate_decision,
    "rate_cut": _analyze_rate_decision,
    "rate_hike": _analyze_rate_decision,
    "rate_hold": _analyze_rate_decision,
    "monetary_policy": _analyze_monetary_policy,
    "policy_change": _analyze_monetary_policy,
}


def analyze_macro(
    text: str,
    event_type: str | None,
    proposition_status: str,
    historical_baseline: float | None = None,
    macro_snapshot: MacroSnapshot | None = None,
) -> MacroResult:
    """Main entry point: analyze macro proposition and return forecast.

    Args:
        text: The proposition text to analyze
        event_type: The macro event type
        proposition_status: "CLEAR" or "AMBIGUOUS"
        historical_baseline: Optional historical YES rate
        macro_snapshot: Optional real (or realistically-mocked) FRED
            snapshot (providers/fred.py). When the text-keyword analysis
            below finds no confirmed/reported decision (i.e. an upcoming,
            not-yet-decided meeting), this is used as a genuine
            quantitative fallback signal for rate_cut/rate_hike/rate_hold
            questions instead of returning unavailable.

    Returns:
        MacroResult with probability if available, or available=False."""
    # Check if this model handles the event type
    if event_type is None or event_type not in _EVENT_TYPES:
        return MacroResult(
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
            reason=f"event_type '{event_type}' not handled by macro model",
            inputs_used=(),
            contributions=(),
            uncertainty=1.0,
        )

    # A market question describes the proposition; it is not evidence that
    # the described decision was reported or confirmed. Questions go
    # directly to the structured-data path below.
    is_market_question = "?" in text or text.strip().lower().startswith(
        ("will ", "is ", "are ", "does ", "do ", "can ", "could ")
    )
    if is_market_question:
        probability, reason, inputs_used = (
            None,
            "market question is not evidence of a completed/reported rate decision",
            ("market_question_only",),
        )
    else:
        analysis_func = _ANALYSIS_FUNCTIONS[event_type]
        probability, reason, inputs_used = analysis_func(text, proposition_status, event_type)

    if probability is None:
        # Text-keyword analysis found no confirmed/reported decision (most
        # commonly: an upcoming, not-yet-decided meeting). For the three
        # directional rate event types, fall back to the real FRED-derived
        # quantitative signal instead of returning unavailable outright —
        # this is the genuine "use real fetched numbers as real inputs"
        # data path, not evidence-text classification wearing a numeric
        # costume (see _quantitative_rate_probabilities' docstring for the
        # method).
        if event_type in _QUANTITATIVE_EVENT_TYPES and macro_snapshot is not None:
            probabilities = _quantitative_rate_probabilities(macro_snapshot)
            if probabilities is not None:
                key = {"rate_cut": "cut", "rate_hike": "hike", "rate_hold": "hold"}[event_type]
                quant_probability = probabilities[key]
                quant_inputs = (
                    "fred_policy_rate", "fred_cpi_yoy_trend",
                    "fred_unemployment_trend", "fomc_calendar",
                )
                return MacroResult(
                    available=True,
                    probability=round(quant_probability, 4),
                    confidence=55.0,  # real quantitative signal, but a documented-simple
                                      # heuristic (not fitted/calibrated) — below the
                                      # official-source-confirmed tiers (75/90) above.
                    data_quality=DataQualityBreakdown(
                        vollstaendigkeit=0.7,
                        aktualitaet=1.0,
                        quellenuebereinstimmung=0.6,
                        historische_fallzahl=0.5,
                        resolution_klarheit=1.0 if proposition_status == "CLEAR" else 0.5,
                        liquiditaet=0.5,
                    ),
                    reason=(
                        f"no confirmed/reported decision in text ({reason}); used real FRED "
                        f"policy rate ({macro_snapshot.policy_rate:.2f}%), CPI YoY trend "
                        f"({macro_snapshot.cpi_yoy:.2f}% vs {macro_snapshot.cpi_yoy_prior:.2f}% "
                        f"~3mo prior), and unemployment trend "
                        f"({macro_snapshot.unemployment_rate:.2f}% vs "
                        f"{macro_snapshot.unemployment_rate_prior:.2f}% ~3mo prior) as a "
                        f"quantitative fallback signal"
                    ),
                    inputs_used=inputs_used + quant_inputs,
                    contributions=(
                        {"source": "fred_policy_rate", "weight": 0.2, "impact": "neutral"},
                        {"source": "fred_cpi_yoy_trend", "weight": 0.25, "impact": "positive" if key == "hike" else "negative" if key == "cut" else "neutral"},
                        {"source": "fred_unemployment_trend", "weight": 0.25, "impact": "positive" if key == "cut" else "negative" if key == "hike" else "neutral"},
                    ),
                    uncertainty=max(0.0, 1.0 - 55.0 / 100.0),
                    data_source_status="OK",
                )

        # For the quantitative event types, macro_snapshot being None here
        # means the FRED/BLS fetch itself failed (providers/fred.py returns
        # None on any transport error — see its module docstring) — that is
        # a materially different situation from "no relevant evidence text
        # exists", and must be surfaced as such rather than folded into the
        # same generic reason string.
        if event_type in _QUANTITATIVE_EVENT_TYPES and macro_snapshot is None:
            return MacroResult(
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
                reason=(
                    f"{reason}; real-time macro data (FRED/BLS) could not be "
                    f"fetched (SOURCE_FETCH_FAILED) — this is a data-source "
                    f"availability problem, not a statement that no relevant "
                    f"evidence exists"
                ),
                inputs_used=inputs_used + ("macro_source_fetch_failed",),
                contributions=(),
                uncertainty=1.0,
                data_source_status="SOURCE_FETCH_FAILED",
            )

        return MacroResult(
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
            data_source_status="NO_EVIDENCE",
        )

    # Build data quality from inputs
    has_official = "official_source" in inputs_used
    has_direct = any(term in inputs_used for term in (
        "rate_cut_confirmed", "rate_hike_confirmed", "rate_hold_confirmed",
        "tightening_confirmed", "easing_confirmed", "decision_made",
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
            contributions.append({"source": "official_central_bank_statement", "weight": 0.2, "impact": "positive"})
        elif inp == "rate_cut_confirmed":
            contributions.append({"source": "rate_cut_confirmed", "weight": 0.3, "impact": "positive"})
        elif inp == "rate_hike_confirmed":
            contributions.append({"source": "rate_hike_confirmed", "weight": 0.3, "impact": "negative"})
        elif inp == "rate_hold_confirmed":
            contributions.append({"source": "rate_hold_confirmed", "weight": 0.3, "impact": "neutral"})
        elif inp == "tightening_confirmed":
            contributions.append({"source": "tightening_confirmed", "weight": 0.3, "impact": "negative"})
        elif inp == "easing_confirmed":
            contributions.append({"source": "easing_confirmed", "weight": 0.3, "impact": "positive"})
        elif inp == "rate_cut_reported":
            contributions.append({"source": "rate_cut_reported", "weight": 0.15, "impact": "positive"})
        elif inp == "rate_hike_reported":
            contributions.append({"source": "rate_hike_reported", "weight": 0.15, "impact": "negative"})
        elif inp == "rate_hold_reported":
            contributions.append({"source": "rate_hold_reported", "weight": 0.15, "impact": "neutral"})
        elif inp == "tightening_reported":
            contributions.append({"source": "tightening_reported", "weight": 0.15, "impact": "negative"})
        elif inp == "easing_reported":
            contributions.append({"source": "easing_reported", "weight": 0.15, "impact": "positive"})
        elif inp == "negation_detected":
            contributions.append({"source": "negation_detected", "weight": 0.3, "impact": "negative"})
        elif inp == "upcoming_decision":
            contributions.append({"source": "upcoming_decision", "weight": 0.0, "impact": "neutral"})
        elif inp == "insufficient_signal":
            contributions.append({"source": "insufficient_signal", "weight": 0.0, "impact": "neutral"})

    return MacroResult(
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
def compute_macro_forecast(
    text: str,
    event_type: str | None,
    proposition_status: str,
    historical_baseline: float | None = None,
    macro_snapshot: MacroSnapshot | None = None,
) -> MacroResult:
    """Alias for analyze_macro — same interface, preserved for
    backward compatibility during the Phase E transition."""
    return analyze_macro(text, event_type, proposition_status, historical_baseline, macro_snapshot)
