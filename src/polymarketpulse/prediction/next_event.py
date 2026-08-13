"""Phase F — Next Event Engine.

Answers, for one market, not just "where do things stand" (Phase E's
Structured World State) but "what is the most likely next resolution-
relevant event". Deliberately narrow: this is a NAMED-STEP lookup into the
market's own real ResolutionPath (Phase B's Market Template Library),
never an LLM guess and never a fabricated event. A market with no known
multi-step template (the overwhelming majority) honestly gets `status=
"UNKNOWN"` / `next_event_type=None` -- there is no invented "next event"
for a simple binary market.

`current_state` is deliberately never selected as a next event: it is the
first entry in every real template (see world_state.py's
`_TEMPLATE_STEPS_BY_EVENT_TYPE`) and describes NOW, not something still to
happen -- treating it as a "next event" would just restate Phase E's
current_state field under a different name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .world_state import ResolutionPath

NextEventStatus = Literal["EXPECTED", "PLAUSIBLE", "BLOCKED", "UNKNOWN", "ALREADY_OCCURRED"]

# A step name that only ever describes the market's current situation, not
# a future occurrence -- never itself eligible to be "the next event".
_NON_EVENT_STEPS = frozenset({"current_state"})

# Human-readable descriptions per (template, step). Deliberately incomplete
# -- steps without a real, curated label here fall back to the raw step
# name rather than a guessed phrase. Extends change_triggers.py's existing
# LEGISLATION-only `_STEP_NAME_DE` to the other three real templates added
# in Phase B.
_STEP_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "LEGISLATION": {
        "introduced": "Einbringung des Gesetzentwurfs",
        "committee": "Abschluss der Ausschussphase",
        "house_vote": "Abstimmung im Repräsentantenhaus",
        "senate_vote": "Abstimmung im Senat",
        "presidential_action": "Unterzeichnung oder Veto durch den Präsidenten",
    },
    "FED": {
        "macro_inputs": "Veröffentlichung der nächsten makroökonomischen Datenpunkte (CPI/PCE/Jobs)",
        "meeting": "nächstes FOMC-Meeting",
        "policy_decision": "Policy-Entscheidung des FOMC",
        "resolution": "endgültige Auflösung der Marktfrage",
    },
    "GEOPOLITICS": {
        "escalation": "weitere Eskalation der Lage",
        "operational_change": "bestätigte operative Änderung (z.B. Schließung, Unterbrechung)",
        "resolution_threshold": "Erreichen der im Markt definierten Auflösungsschwelle",
        "confirmation": "offizielle Bestätigung des Status",
    },
    "PRICE_THRESHOLD": {
        "distance": "Veränderung des Abstands zur Schwelle",
        "volatility": "signifikante Volatilitätsänderung",
        "time_remaining": "Erreichen eines kritischen Zeitfensters vor der Deadline",
        "threshold_event": "Erreichen/Überschreiten der Preisschwelle",
    },
    "SPORTS": {
        "round_match": "nächste Runde/nächstes Spiel",
        "qualification": "Qualifikationsentscheidung",
        "final_outcome": "finales Ergebnis",
    },
}


@dataclass(frozen=True)
class NextEvent:
    next_event_type: str | None
    next_event_description: str | None
    expected_time_window: str | None
    prerequisites: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    supporting_claim_ids: tuple[str, ...] = field(default_factory=tuple)
    source_ids: tuple[str, ...] = field(default_factory=tuple)
    confidence: float | None = None
    resolution_relevance: float | None = None
    status: NextEventStatus = "UNKNOWN"

    def as_dict(self) -> dict:
        return {
            "next_event_type": self.next_event_type,
            "next_event_description": self.next_event_description,
            "expected_time_window": self.expected_time_window,
            "prerequisites": list(self.prerequisites),
            "blockers": list(self.blockers),
            "supporting_claim_ids": list(self.supporting_claim_ids),
            "source_ids": list(self.source_ids),
            "confidence": self.confidence,
            "resolution_relevance": self.resolution_relevance,
            "status": self.status,
        }


def derive_next_event(
    resolution_path: ResolutionPath | None,
    path_step_claims: tuple[dict, ...] = (),
) -> NextEvent:
    """Pure function of the real ResolutionPath (Phase B templates + Phase A/D's
    real PATH_STEP claims already folded into its step statuses) and the raw
    path_step_claims list (for supporting_claim_ids/source_ids -- the real
    claims that most recently updated this path, if any). No new fetch, no
    new probability computation, no LLM.
    """
    if resolution_path is None or not resolution_path.applies:
        return NextEvent(
            next_event_type=None, next_event_description=None, expected_time_window=None, status="UNKNOWN",
        )

    steps = resolution_path.steps
    candidates = [s for s in steps if s.name not in _NON_EVENT_STEPS]
    if not candidates or all(s.status == "completed" for s in candidates):
        # Every real, event-eligible step is already confirmed complete --
        # the resolution path itself is exhausted; there is no further
        # named step left to wait for (the market's final outcome is the
        # only thing left, not a "next event" in the path sense).
        return NextEvent(
            next_event_type=None, next_event_description="Alle bekannten Resolution-Schritte abgeschlossen.",
            expected_time_window=None, status="ALREADY_OCCURRED",
            resolution_relevance=1.0 if candidates else None,
        )

    next_step = next(s for s in candidates if s.status != "completed")
    step_index = steps.index(next_step)
    prerequisites = tuple(
        s.name for s in steps[:step_index] if s.status != "completed" and s.name not in _NON_EVENT_STEPS
    )

    labels = _STEP_DESCRIPTIONS.get(resolution_path.template_name, {})
    description = labels.get(next_step.name, next_step.name)

    blockers = tuple(b for b in resolution_path.blockers) if next_step.status == "blocked" else ()

    if next_step.status == "blocked":
        status: NextEventStatus = "BLOCKED"
        confidence = 0.3
    elif next_step.status == "in_progress":
        status = "EXPECTED"
        confidence = 0.6
    elif prerequisites:
        # An earlier real step is itself still open -- this step cannot be
        # meaningfully "next" yet, structurally implausible before that.
        status = "PLAUSIBLE"
        confidence = 0.3
    else:
        status = "PLAUSIBLE"
        confidence = 0.5

    expected_time_window = (
        "kurzfristig (hoher Deadline-Druck)" if resolution_path.deadline_pressure in ("HIGH", "CRITICAL") else None
    )

    matching_claims = [c for c in path_step_claims if c.get("resolution_step") == next_step.name]

    return NextEvent(
        next_event_type=next_step.name.upper(),
        next_event_description=description,
        expected_time_window=expected_time_window,
        prerequisites=prerequisites,
        blockers=blockers,
        supporting_claim_ids=tuple(dict.fromkeys(c["claim_id"] for c in matching_claims if c.get("claim_id"))),
        source_ids=tuple(dict.fromkeys(c["source"] for c in matching_claims if c.get("source"))),
        confidence=confidence,
        resolution_relevance=1.0,  # a step ON the real resolution path is by construction resolution-relevant
        status=status,
    )
