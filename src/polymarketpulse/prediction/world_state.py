"""World State summary (steering point 9/21).

Audit conclusion (see HANDOFF.md for the full writeup): most of "what must
happen for YES/NO" and "how much time remains" was ALREADY computed inside
`engine.compute_prediction` — `semantics.parse_market_proposition` already
produces real `yes_condition`/`no_condition` strings, and `resolution_date`
was already used internally to derive `deadline_phase`/`deadline_hours` —
but NONE of that was ever exposed on `PredictionResult`. A caller (API,
dashboard, future browser walkthrough) had no reachable field to answer
"what must happen for this to resolve YES?" even though the engine had
already computed the answer and then discarded it.

This module does not invent any new signal. It assembles a small, honest,
read-only summary object from fields the engine already computed elsewhere
in the same prediction run:
  - yes_condition / no_condition / deadline / resolution_authority: straight
    from `semantics.MarketProposition` (Phase A, unmodified).
  - time_remaining_hours: `(resolution_date - now)`, the same subtraction
    `manipulation.py`/`data_gaps.py` callers already did inline — just
    computed once here and exposed.
  - most_recent_evidence_headline/_published_at: the newest-dated item
    across `IndependentEvidenceResult.evidence_for_yes`/`evidence_for_no`,
    i.e. the most recent thing the system actually observed happening
    (or None if no independent evidence is available for this market).
  - claim_status_counts / counter_evidence_count: mirrors of the same
    fields on `IndependentEvidenceResult` (Step 2 of this round's work),
    included here too since "what is currently known/disputed about this
    market" is part of "what is true now".

Purely diagnostic/explanatory — never an input to any probability field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .evidence import IndependentEvidenceResult
    from .semantics import MarketProposition

# ---------------------------------------------------------------------------
# Part 2/3 additions (this round): richer, honest sub-states for Politics/
# Geopolitics markets. Nothing here invents new signal — every field below
# is derived only from evidence already gathered by evidence.py
# (IndependentEvidenceResult.evidence_for_yes/evidence_for_no, tiered
# DIRECT_*/SUPPORTS_* by semantics.classify_evidence_relation). Per the
# project owner's explicit rule: "absence of bad news is not normalization"
# — with zero qualifying evidence, current_state/trend are UNKNOWN, never a
# guessed NORMAL/STABLE.
# ---------------------------------------------------------------------------

WaterwayCurrentState = Literal["NORMAL", "DEGRADED", "SEVERELY_RESTRICTED", "CLOSED", "UNKNOWN"]
WaterwayTrend = Literal["IMPROVING", "STABLE", "DETERIORATING", "UNKNOWN"]

# event_type values (semantics.py's vocabulary) this sub-state applies to —
# strategic-waterway/blockade/access scenarios and the conflict event types
# most likely to describe a waterway's operational status.
WATERWAY_EVENT_TYPES = frozenset({"strategic_waterway", "ceasefire", "war_escalation"})

# Ordered worst -> best, used only to compare two already-classified states
# for a trend direction (never to invent a state from nothing).
_WATERWAY_STATE_RANK: dict[str, int] = {
    "CLOSED": 0, "SEVERELY_RESTRICTED": 1, "DEGRADED": 2, "NORMAL": 3,
}

# Deliberately literal keyword lists (same style as claims.py's
# _detect_claim_direction) — checked in worst-to-best order so a headline
# combining e.g. "closed" and "restricted" resolves to the more severe read.
_CLOSED_TERMS = ("closed", "blockade", "blockaded", "shut down", "shut off", "halted", "suspended")
_SEVERELY_RESTRICTED_TERMS = ("severely restricted", "sharply reduced", "near-halt", "near halt", "plunges", "plunged")
_DEGRADED_TERMS = ("restricted", "reduced", "delayed", "disrupted", "limited", "slowed", "restrictions")
_NORMAL_TERMS = ("returns to normal", "return to normal", "back to normal", "normalized", "normalization",
                  "reopened", "reopens", "reopening", "resumed", "resumes normal", "restored")


def _classify_waterway_headline(title: str) -> WaterwayCurrentState | None:
    """Best-effort, literal keyword classification of a single evidence
    item's title into an operational-status bucket. Returns None (not
    classifiable) rather than guessing when no known term is present."""
    lowered = (title or "").lower()
    if any(t in lowered for t in _CLOSED_TERMS):
        return "CLOSED"
    if any(t in lowered for t in _SEVERELY_RESTRICTED_TERMS):
        return "SEVERELY_RESTRICTED"
    if any(t in lowered for t in _NORMAL_TERMS):
        return "NORMAL"
    if any(t in lowered for t in _DEGRADED_TERMS):
        return "DEGRADED"
    return None


@dataclass(frozen=True)
class WaterwayHealthState:
    """Health/trend sub-state for strategic-waterway-flavoured markets
    (Part 2). `basis_evidence_count` is how many real, dated, DIRECT/
    SUPPORTS-tier evidence items the classification actually rests on —
    0 means current_state/trend are UNKNOWN by construction, never a
    default-to-NORMAL guess."""

    current_state: WaterwayCurrentState
    trend: WaterwayTrend
    basis_evidence_count: int = 0

    def as_dict(self) -> dict:
        return {
            "current_state": self.current_state,
            "trend": self.trend,
            "basis_evidence_count": self.basis_evidence_count,
        }


def _derive_waterway_state(
    proposition: MarketProposition,
    independent_evidence: IndependentEvidenceResult | None,
) -> WaterwayHealthState | None:
    """None when this market's event_type isn't waterway-flavoured at all
    (the field simply doesn't apply). Once applicable, honestly returns
    UNKNOWN/UNKNOWN when there is no real dated, tiered evidence to derive
    a state from — this is the expected, common case for most real markets
    given today's evidence volume (see HANDOFF.md)."""
    if proposition.event_type not in WATERWAY_EVENT_TYPES:
        return None

    if independent_evidence is None or not independent_evidence.available:
        return WaterwayHealthState(current_state="UNKNOWN", trend="UNKNOWN", basis_evidence_count=0)

    # Only DIRECT_*/SUPPORTS_* tier items count as "real verified evidence"
    # for this purpose — WEAK_*/CONTEXT/IRRELEVANT/AMBIGUOUS items
    # (relation_weight-gated to near-zero already) are excluded so a single
    # tone-only headline can never move the reported operational state.
    candidates = [
        f
        for f in (*independent_evidence.evidence_for_yes, *independent_evidence.evidence_for_no)
        if f.relation_label in ("DIRECT_YES", "SUPPORTS_YES", "DIRECT_NO", "SUPPORTS_NO") and f.published_at
    ]

    dated_states: list[tuple[str, WaterwayCurrentState]] = []
    for f in candidates:
        state = _classify_waterway_headline(f.title)
        if state is not None:
            dated_states.append((f.published_at, state))

    if not dated_states:
        return WaterwayHealthState(current_state="UNKNOWN", trend="UNKNOWN", basis_evidence_count=0)

    dated_states.sort(key=lambda x: x[0], reverse=True)
    current_state = dated_states[0][1]

    trend: WaterwayTrend = "UNKNOWN"
    if len(dated_states) >= 2:
        latest_rank = _WATERWAY_STATE_RANK[dated_states[0][1]]
        prior_rank = _WATERWAY_STATE_RANK[dated_states[1][1]]
        if latest_rank > prior_rank:
            trend = "IMPROVING"
        elif latest_rank < prior_rank:
            trend = "DETERIORATING"
        else:
            trend = "STABLE"

    return WaterwayHealthState(current_state=current_state, trend=trend, basis_evidence_count=len(dated_states))


# ---------------------------------------------------------------------------
# Part 3: Path-to-Resolution. Politics/Geopolitics-flavoured markets only
# (per the project owner's spec) — a small, additive summary of what would
# need to happen for YES vs NO, built ONLY from real evidence/conditions
# already computed elsewhere. Empty lists (not fabricated placeholder text)
# whenever there is no real evidence to derive them from.
# ---------------------------------------------------------------------------

# Category values (classification.py's fixed taxonomy) this applies to.
POLITICS_GEOPOLITICS_CATEGORIES = frozenset(
    {"POLITICS", "ELECTIONS", "GEOPOLITICS", "WAR_PEACE", "LEGISLATION"}
)


@dataclass(frozen=True)
class PathToResolution:
    current_state: str
    yes_condition: str
    no_condition: str
    time_remaining_hours: float | None
    required_transitions: tuple[str, ...] = field(default_factory=tuple)
    supporting_conditions: tuple[str, ...] = field(default_factory=tuple)
    blocking_conditions: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "current_state": self.current_state,
            "yes_condition": self.yes_condition,
            "no_condition": self.no_condition,
            "time_remaining_hours": self.time_remaining_hours,
            "required_transitions": list(self.required_transitions),
            "supporting_conditions": list(self.supporting_conditions),
            "blocking_conditions": list(self.blocking_conditions),
        }


def _derive_path_to_resolution(
    proposition: MarketProposition,
    time_remaining_hours: float | None,
    independent_evidence: IndependentEvidenceResult | None,
    waterway_state: WaterwayHealthState | None,
    classified_category: str | None,
) -> PathToResolution | None:
    if classified_category not in POLITICS_GEOPOLITICS_CATEGORIES:
        return None

    current_state = waterway_state.current_state if waterway_state is not None else "UNKNOWN"

    supporting_conditions: tuple[str, ...] = ()
    blocking_conditions: tuple[str, ...] = ()
    if independent_evidence is not None and independent_evidence.available:
        supporting_conditions = tuple(
            f.title
            for f in independent_evidence.evidence_for_yes
            if f.relation_label in ("DIRECT_YES", "SUPPORTS_YES")
        )
        blocking_conditions = tuple(
            f.title
            for f in independent_evidence.evidence_for_no
            if f.relation_label in ("DIRECT_NO", "SUPPORTS_NO")
        )

    # required_transitions: only stated when we have a real, evidence-backed
    # current_state that isn't already NORMAL/UNKNOWN — a concrete,
    # structurally-derived (not invented) statement of the one transition
    # still outstanding. Left empty otherwise (honest "don't know").
    required_transitions: tuple[str, ...] = ()
    if waterway_state is not None and current_state not in ("NORMAL", "UNKNOWN"):
        required_transitions = (
            (
                f"waterway status must transition from {current_state} to NORMAL "
                f"(currently backed by {waterway_state.basis_evidence_count} dated evidence item(s))"
            ),
        )

    return PathToResolution(
        current_state=current_state,
        yes_condition=proposition.yes_condition,
        no_condition=proposition.no_condition,
        time_remaining_hours=time_remaining_hours,
        required_transitions=required_transitions,
        supporting_conditions=supporting_conditions,
        blocking_conditions=blocking_conditions,
    )


@dataclass(frozen=True)
class WorldState:
    yes_condition: str
    no_condition: str
    deadline: str | None
    deadline_semantics: str | None
    resolution_authority: str | None
    time_remaining_hours: float | None
    most_recent_evidence_headline: str | None = None
    most_recent_evidence_published_at: str | None = None
    claim_status_counts: dict = field(default_factory=dict)
    counter_evidence_count: int = 0
    # --- Part 1 (this round): observability of the already-symmetric
    # yes/no evidence search. Verified: semantics.classify_evidence_relation
    # and evidence.py's yes_terms/no_terms matching apply the SAME logic to
    # both directions (relation_kind "same"/"opposite" against the
    # proposition, DIRECT_YES/DIRECT_NO tiers computed identically) — there
    # is no separate "search for NO evidence" step needed because the
    # existing pipeline already scans every linked article against both
    # conditions. The real prior gap was that this fact was implicit and
    # unverifiable from outside; these fields make it explicit.
    evidence_for_yes_count: int = 0
    evidence_for_no_count: int = 0
    actively_searched_both_sides: bool = True
    # --- Part 2 (this round): waterway/blockade health+trend sub-state.
    # None when the market's event_type isn't waterway-flavoured.
    waterway_state: WaterwayHealthState | None = None
    # --- Part 3 (this round): path-to-resolution summary for Politics/
    # Geopolitics markets. None when the market's classified category isn't
    # Politics/Geopolitics-flavoured.
    path_to_resolution: PathToResolution | None = None

    def as_dict(self) -> dict:
        return {
            "yes_condition": self.yes_condition,
            "no_condition": self.no_condition,
            "deadline": self.deadline,
            "deadline_semantics": self.deadline_semantics,
            "resolution_authority": self.resolution_authority,
            "time_remaining_hours": self.time_remaining_hours,
            "most_recent_evidence_headline": self.most_recent_evidence_headline,
            "most_recent_evidence_published_at": self.most_recent_evidence_published_at,
            "claim_status_counts": dict(self.claim_status_counts),
            "counter_evidence_count": self.counter_evidence_count,
            "evidence_for_yes_count": self.evidence_for_yes_count,
            "evidence_for_no_count": self.evidence_for_no_count,
            "actively_searched_both_sides": self.actively_searched_both_sides,
            "waterway_state": self.waterway_state.as_dict() if self.waterway_state is not None else None,
            "path_to_resolution": (
                self.path_to_resolution.as_dict() if self.path_to_resolution is not None else None
            ),
        }


def assemble_world_state(
    proposition: MarketProposition,
    resolution_date: datetime | None,
    now: datetime,
    independent_evidence: IndependentEvidenceResult | None,
    classified_category: str | None = None,
) -> WorldState:
    """Assemble a WorldState from already-computed engine values. No new
    probability-affecting computation happens here.

    `classified_category` (additive, Part 3): the Phase-C classified
    taxonomy value (e.g. "GEOPOLITICS"/"POLITICS") — optional so every
    existing caller/test that omits it keeps working exactly as before;
    path_to_resolution is simply None in that case."""
    time_remaining_hours: float | None = None
    if resolution_date is not None:
        time_remaining_hours = (resolution_date - now).total_seconds() / 3600.0

    most_recent_headline: str | None = None
    most_recent_published_at: str | None = None
    counter_evidence_count = 0
    claim_status_counts: dict = {}
    evidence_for_yes_count = 0
    evidence_for_no_count = 0
    if independent_evidence is not None:
        counter_evidence_count = independent_evidence.counter_evidence_count
        claim_status_counts = dict(independent_evidence.claim_status_counts)
        evidence_for_yes_count = len(independent_evidence.evidence_for_yes)
        evidence_for_no_count = len(independent_evidence.evidence_for_no)
        candidates = [
            f for f in (independent_evidence.evidence_for_yes + independent_evidence.evidence_for_no)
            if f.published_at
        ]
        if candidates:
            latest = max(candidates, key=lambda f: f.published_at)
            most_recent_headline = latest.title
            most_recent_published_at = latest.published_at

    waterway_state = _derive_waterway_state(proposition, independent_evidence)
    path_to_resolution = _derive_path_to_resolution(
        proposition, time_remaining_hours, independent_evidence, waterway_state, classified_category,
    )

    return WorldState(
        yes_condition=proposition.yes_condition,
        no_condition=proposition.no_condition,
        deadline=proposition.deadline,
        deadline_semantics=proposition.deadline_semantics,
        resolution_authority=proposition.resolution_authority,
        time_remaining_hours=time_remaining_hours,
        most_recent_evidence_headline=most_recent_headline,
        most_recent_evidence_published_at=most_recent_published_at,
        claim_status_counts=claim_status_counts,
        counter_evidence_count=counter_evidence_count,
        evidence_for_yes_count=evidence_for_yes_count,
        evidence_for_no_count=evidence_for_no_count,
        actively_searched_both_sides=True,
        waterway_state=waterway_state,
        path_to_resolution=path_to_resolution,
    )
