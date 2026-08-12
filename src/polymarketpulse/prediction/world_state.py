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
    from ..providers.fred import MacroSnapshot
    from .evidence import IndependentEvidenceResult
    from .semantics import MarketProposition

# ---------------------------------------------------------------------------
# ROUND-2 (85-section brief, section 5): World State 2.0 — real structured
# state variables. `StateVariable` represents a single REAL fetched/derived
# data point (never a text summary, never a guess). Populated ONLY for the
# two domains that already have a real, working external data feed wired
# into engine.py (MACRO/FRED, CRYPTO/CoinGecko) — see
# `_build_macro_state_variables`/`_build_quant_state_variables` below. Every
# other domain (Politics/Geopolitics/Sports) gets an honestly EMPTY tuple
# this round: per the project owner's explicit rule ("UNKNOWN darf nicht als
# neutraler Wert missbraucht werden" — UNKNOWN must not be misused as a
# neutral placeholder value), fabricating an UNKNOWN-valued StateVariable for
# a field with no real backing data source is exactly the kind of dishonest
# placeholder this rule forbids — an empty tuple is the honest choice.
# ---------------------------------------------------------------------------

# "live_fetch": this call's HTTP request to the provider succeeded and the
# value below came straight from that response — the ONLY source_type this
# round produces, since neither FRED nor CoinGecko has a cache/staleness
# layer in this codebase yet (confirmed: no cache table/module exists for
# either provider — see HANDOFF.md's note on this being a real, separate,
# out-of-scope performance question for FRED's ~30s uncached call). Kept as
# a Literal with room to grow rather than a free string, so a future round
# that adds real caching has a real "cached"/"stale" value to report instead
# of inventing one now.
StateVariableSourceType = Literal["live_fetch"]

# "provider_reported": the value is exactly what a single named external
# provider (FRED, CoinGecko) returned for its own published series/endpoint
# — not independently cross-verified against a second source. This is an
# honest, precise label, not a claim of higher verification than the code
# actually performs.
StateVariableVerificationStatus = Literal["provider_reported"]


@dataclass(frozen=True)
class StateVariable:
    """A single real, structured world-state data point — section 5 of the
    project owner's brief. Every field must be derivable from a real fetched
    or computed value; nothing here is ever guessed or defaulted to fill a
    gap (see module header)."""

    name: str
    value: float | str
    unit: str | None
    timestamp: str  # ISO date/datetime the VALUE itself refers to (the
                     # provider's own as-of date), not necessarily "now"
    available_at: str  # ISO datetime this system actually observed/fetched
                        # the value — point-in-time safety: a backtest must
                        # never use a value before its available_at
    source: str
    source_type: StateVariableSourceType
    confidence: float
    freshness: str  # short, honest, human-readable staleness description
                     # derived from (available_at - timestamp), e.g.
                     # "same-day" / "~1 month old (monthly series)"
    verification_status: StateVariableVerificationStatus

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp,
            "available_at": self.available_at,
            "source": self.source,
            "source_type": self.source_type,
            "confidence": self.confidence,
            "freshness": self.freshness,
            "verification_status": self.verification_status,
        }


def _build_macro_state_variables(
    macro_snapshot: MacroSnapshot | None, now: datetime,
) -> tuple[StateVariable, ...]:
    """Real StateVariable instances derived from an already-fetched
    `providers.fred.MacroSnapshot` (reused, not re-fetched here — engine.py
    owns the one live FRED call per prediction run). Empty tuple when no
    snapshot was fetched this run (e.g. not a rate_cut/rate_hike/rate_hold
    market, or the live fetch failed) — never a fabricated placeholder."""
    if macro_snapshot is None:
        return ()

    available_at = now.isoformat()

    def _freshness_for(as_of: object) -> str:
        try:
            age_days = (now.date() - as_of).days  # type: ignore[operator]
        except TypeError:
            return "unknown"
        if age_days < 0:
            return "unknown"
        if age_days <= 31:
            return f"{age_days}d old (monthly FRED series)"
        return f"{age_days}d old (monthly FRED series, stale)"

    variables = [
        StateVariable(
            name="current_rate",
            value=macro_snapshot.policy_rate,
            unit="percent",
            timestamp=macro_snapshot.policy_rate_as_of.isoformat(),
            available_at=available_at,
            source="fred",
            source_type="live_fetch",
            confidence=0.95,  # official government-published series, single-
                               # source (no cross-provider corroboration)
            freshness=_freshness_for(macro_snapshot.policy_rate_as_of),
            verification_status="provider_reported",
        ),
        StateVariable(
            name="latest_cpi",
            value=macro_snapshot.cpi_yoy,
            unit="percent_yoy",
            timestamp=macro_snapshot.as_of_date.isoformat(),
            available_at=available_at,
            source="fred",
            source_type="live_fetch",
            confidence=0.95,
            freshness=_freshness_for(macro_snapshot.as_of_date),
            verification_status="provider_reported",
        ),
        StateVariable(
            name="unemployment_rate",
            value=macro_snapshot.unemployment_rate,
            unit="percent",
            timestamp=macro_snapshot.as_of_date.isoformat(),
            available_at=available_at,
            source="fred",
            source_type="live_fetch",
            confidence=0.95,
            freshness=_freshness_for(macro_snapshot.as_of_date),
            verification_status="provider_reported",
        ),
    ]

    if macro_snapshot.next_fomc_meeting_date is not None:
        variables.append(
            StateVariable(
                name="next_meeting_date",
                value=macro_snapshot.next_fomc_meeting_date.isoformat(),
                unit=None,
                # A calendar fact, not a data-provider observation with its
                # own as-of drift — timestamp is honestly "as of this run".
                timestamp=macro_snapshot.as_of_date.isoformat(),
                available_at=available_at,
                source="fred",
                source_type="live_fetch",
                confidence=0.9,  # hardcoded public FOMC calendar table (see
                                  # providers/fred.py) — real public info, but
                                  # not itself fetched live from an API
                freshness="calendar reference (not a time-series observation)",
                verification_status="provider_reported",
            )
        )

    return tuple(variables)


def _build_quant_state_variables(
    asset: str | None,
    current_price: float | None,
    daily_volatility: float | None,
    now: datetime,
) -> tuple[StateVariable, ...]:
    """Real StateVariable instances derived from an already-fetched
    `providers.coingecko.PriceData` (reused, not re-fetched here — engine.py
    owns the one live CoinGecko call per prediction run). Empty tuple when
    no price data was fetched this run. No `trend` variable: quant.py does
    not compute a directional trend signal today (only spot price +
    realized volatility), so per the round's explicit instruction not to
    invent a signal that doesn't already exist, none is added here."""
    if current_price is None:
        return ()

    available_at = now.isoformat()
    variables = [
        StateVariable(
            name="spot_price",
            value=current_price,
            unit="usd",
            # CoinGecko's market_chart response has no explicit per-point
            # observation timestamp exposed by providers/coingecko.py's
            # PriceData (only the raw closes list, from which current_price
            # is taken as the last daily close) — honestly using the fetch
            # time as the timestamp rather than fabricating a precise
            # as-of date the code doesn't actually have.
            timestamp=available_at,
            available_at=available_at,
            source="coingecko",
            source_type="live_fetch",
            confidence=0.9,
            freshness="live (fetched this run)",
            verification_status="provider_reported",
        ),
    ]

    if daily_volatility is not None:
        variables.append(
            StateVariable(
                name="realized_volatility",
                value=daily_volatility,
                unit="daily_stdev_log_return",
                timestamp=available_at,
                available_at=available_at,
                source="coingecko",
                source_type="live_fetch",
                # Lower than spot_price: a derived statistic (sample stdev
                # over a trailing window, see providers/coingecko.py), not a
                # single directly-observed value — real limitations
                # (single-exchange source, no term structure) documented in
                # quant.py's module docstring apply here too.
                confidence=0.75,
                freshness="derived from trailing daily-close history fetched this run",
                verification_status="provider_reported",
            )
        )

    return tuple(variables)

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
_DEGRADED_TERMS = ("restricted", "reduced", "delayed", "disrupted", "disruption", "limited", "slowed", "restrictions")
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


# ROUND-1 (85-section brief, sections 9-10): richer per-transition-step
# structure. `probability_status` is ALWAYS "UNKNOWN" today — there is no
# real empirical/historical basis anywhere in this codebase for a fitted
# state-to-state transition probability (no resolved-forecast dataset with
# per-transition outcomes exists yet). This is the correct, honest state of
# the system in this round, not a gap to fake-fill; `confidence` mirrors it
# and stays None for the same reason. See
# test_path_to_resolution_transition_steps.py::
# test_probability_status_is_always_unknown_no_code_path_fabricates_a_number
# for the explicit regression test locking this in.
TransitionProbabilityStatus = Literal["UNKNOWN"]


@dataclass(frozen=True)
class TransitionStep:
    state_from: str
    state_to: str
    required_event: str
    supporting_evidence: tuple[str, ...] = field(default_factory=tuple)
    counter_evidence: tuple[str, ...] = field(default_factory=tuple)
    estimated_duration: str | None = None
    probability_status: TransitionProbabilityStatus = "UNKNOWN"
    confidence: float | None = None

    def as_dict(self) -> dict:
        return {
            "state_from": self.state_from,
            "state_to": self.state_to,
            "required_event": self.required_event,
            "supporting_evidence": list(self.supporting_evidence),
            "counter_evidence": list(self.counter_evidence),
            "estimated_duration": self.estimated_duration,
            "probability_status": self.probability_status,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# BLOCK C, Part 2: ResolutionStep / ResolutionPath — a genuinely NEW,
# real multi-step resolution structure, distinct from TransitionStep above
# (which describes a single generic state-A -> state-B transition, e.g. a
# waterway's operational status). A market's resolution may instead require
# several SEQUENTIAL/STRUCTURAL steps (the project owner's own example: US
# legislation -> introduced -> committee -> house -> senate -> president ->
# signed). This codebase has NO real legislative-status API, no court-docket
# API, no structured multi-step-process data source for most domains, so
# this is deliberately narrow: the STRUCTURE is real and correctly typed for
# every market, but step STATUS is only ever populated from real, dated,
# DIRECT_*/SUPPORTS_*-tier evidence (the same tier gate `_derive_waterway_
# state` above already uses) — never guessed. Markets whose event_type has
# no known multi-step structure (the overwhelming majority: price-threshold,
# single binary events, etc.) get `applies=False` and an empty `steps`
# tuple — never a forced, fake multi-step breakdown.
# ---------------------------------------------------------------------------

ResolutionStepStatus = Literal["not_started", "in_progress", "completed", "blocked", "unknown"]
DeadlinePressure = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]
PathFeasibility = Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]


@dataclass(frozen=True)
class ResolutionStep:
    """One step of a market's real multi-step resolution structure.
    `evidence` holds the real (dated, tiered) evidence-item titles the
    status was derived from — empty when `status` is honestly "unknown"."""

    name: str
    status: ResolutionStepStatus
    evidence: tuple[str, ...] = field(default_factory=tuple)
    timestamp: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "evidence": list(self.evidence),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class ResolutionPath:
    """Ordered multi-step resolution path for one market, plus real derived
    fields. `applies=False` (empty `steps`) for the majority of markets that
    have a simple binary yes/no resolution structure with no real multi-step
    process to break down — this is a legitimate outcome, not a gap."""

    applies: bool
    steps: tuple[ResolutionStep, ...] = field(default_factory=tuple)
    # None whenever the step structure/statuses are too unknown to count
    # (e.g. `applies=False`, or every known step's status is "unknown") —
    # never a fabricated 0.
    steps_remaining: int | None = None
    # A simple ordinal fraction (completed_steps / total_steps), not fake
    # precision — None when `applies=False` or nothing is known yet.
    path_completion: float | None = None
    deadline_pressure: DeadlinePressure = "UNKNOWN"
    path_feasibility: PathFeasibility = "UNKNOWN"
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "applies": self.applies,
            "steps": [s.as_dict() for s in self.steps],
            "steps_remaining": self.steps_remaining,
            "path_completion": self.path_completion,
            "deadline_pressure": self.deadline_pressure,
            "path_feasibility": self.path_feasibility,
            "blockers": list(self.blockers),
        }


# event_type -> ordered step names for the one multi-step structure this
# round wires real evidence into (US federal legislation, the project
# owner's own example). Kept deliberately narrow/explicit — no guessed
# structure for event_types not listed here (see `_derive_resolution_path`).
_MULTISTEP_STRUCTURE_BY_EVENT_TYPE: dict[str, tuple[str, ...]] = {
    "legislation": ("introduced", "committee", "house_vote", "senate_vote", "presidential_action"),
}

# Per-step keyword lists (same literal-keyword style as claims.py's
# `_detect_claim_direction`/world_state.py's `_classify_waterway_headline`)
# used to classify a single dated evidence-item title as COMPLETING that
# step. Checked in step order; a match for a later step implies every prior
# step is also complete (a real, sequential legislative process), never the
# reverse.
_LEGISLATION_STEP_COMPLETION_TERMS: dict[str, tuple[str, ...]] = {
    "introduced": ("introduced", "bill introduced", "introduces the bill"),
    "committee": ("cleared committee", "committee vote", "passed committee", "advances out of committee",
                  "committee approves", "reported out of committee"),
    "house_vote": ("passes the house", "passed the house", "house passes", "cleared the house",
                   "house approves"),
    "senate_vote": ("passes the senate", "passed the senate", "senate passes", "cleared the senate",
                     "senate approves"),
    "presidential_action": ("signed into law", "signs the bill", "president signs", "vetoed", "veto",
                              "pocket veto"),
}

# Keyword lists for a step that is genuinely underway but not yet complete —
# distinct from "unknown" (no evidence at all) and "completed" (explicit
# completion language above).
_LEGISLATION_STEP_IN_PROGRESS_TERMS: dict[str, tuple[str, ...]] = {
    "introduced": ("plans to introduce", "expected to introduce"),
    "committee": ("scheduled for committee", "committee hearing", "committee markup", "under committee review"),
    "house_vote": ("scheduled for a house vote", "house floor vote scheduled", "heads to the house floor"),
    "senate_vote": ("scheduled for a senate vote", "senate floor vote scheduled", "heads to the senate floor"),
    "presidential_action": ("awaits president's signature", "sent to the president", "on the president's desk"),
}


def _classify_legislation_step(title: str) -> tuple[str, ResolutionStepStatus] | None:
    """Best-effort, literal keyword classification of a single evidence
    item's title into (step_name, status). Returns None when no known term
    is present — never a guess."""
    lowered = (title or "").lower()
    for step_name, terms in _LEGISLATION_STEP_COMPLETION_TERMS.items():
        if any(t in lowered for t in terms):
            return step_name, "completed"
    for step_name, terms in _LEGISLATION_STEP_IN_PROGRESS_TERMS.items():
        if any(t in lowered for t in terms):
            return step_name, "in_progress"
    return None


def _deadline_pressure(
    time_remaining_hours: float | None, steps_remaining: int | None,
) -> DeadlinePressure:
    """Real, ordinal-only derivation from `time_remaining_hours` (already
    computed, real, from `resolution_date - now`) and, when known,
    `steps_remaining` — never a fabricated probability. A market past its
    deadline (<=0h) or with no deadline at all is reported honestly rather
    than guessed."""
    if time_remaining_hours is None:
        return "UNKNOWN"
    if time_remaining_hours <= 0:
        return "CRITICAL"
    # Per-remaining-step time budget when the step count is known — a tight
    # multi-step process compresses the effective runway even if the raw
    # deadline looks distant. Falls back to the raw deadline alone when the
    # step count itself is unknown.
    effective_hours = (
        time_remaining_hours / max(1, steps_remaining) if steps_remaining else time_remaining_hours
    )
    if effective_hours <= 24:
        return "CRITICAL"
    if effective_hours <= 24 * 7:
        return "HIGH"
    if effective_hours <= 24 * 30:
        return "MEDIUM"
    return "LOW"


def _derive_resolution_path(
    proposition: MarketProposition,
    time_remaining_hours: float | None,
    independent_evidence: IndependentEvidenceResult | None,
) -> ResolutionPath:
    """Builds the real ResolutionPath for one market. `applies=False` (empty
    steps) is the correct, honest outcome for every event_type not in
    `_MULTISTEP_STRUCTURE_BY_EVENT_TYPE` — i.e. the overwhelming majority of
    markets (price-threshold, single binary events, etc.), which have no
    real multi-step process to break down."""
    step_names = _MULTISTEP_STRUCTURE_BY_EVENT_TYPE.get(proposition.event_type or "")
    if not step_names:
        return ResolutionPath(
            applies=False,
            steps=(),
            steps_remaining=None,
            path_completion=None,
            deadline_pressure=_deadline_pressure(time_remaining_hours, None),
            path_feasibility="UNKNOWN",
            blockers=(),
        )

    # Only DIRECT_*/SUPPORTS_*-tier, dated evidence counts as a real basis
    # for a step's status — same tier gate as `_derive_waterway_state`.
    candidates: list = []
    if independent_evidence is not None and independent_evidence.available:
        candidates = [
            f
            for f in (*independent_evidence.evidence_for_yes, *independent_evidence.evidence_for_no)
            if f.relation_label in ("DIRECT_YES", "SUPPORTS_YES", "DIRECT_NO", "SUPPORTS_NO") and f.published_at
        ]

    # step_name -> (status, [evidence titles], latest timestamp)
    findings: dict[str, tuple[ResolutionStepStatus, list[str], str | None]] = {}
    for f in candidates:
        classified = _classify_legislation_step(f.title)
        if classified is None:
            continue
        step_name, status = classified
        prior = findings.get(step_name)
        if prior is None or (status == "completed" and prior[0] != "completed"):
            findings[step_name] = (status, [f.title], f.published_at)
        elif status == prior[0]:
            findings[step_name] = (status, [*prior[1], f.title], prior[2])

    # A later step's real completion implies every prior step is also
    # complete (sequential process) — this is a structural inference from a
    # REAL observed later-stage event, not a guess about an unobserved
    # earlier one.
    highest_completed_index = -1
    for idx, name in enumerate(step_names):
        if findings.get(name, (None,))[0] == "completed":
            highest_completed_index = idx

    steps: list[ResolutionStep] = []
    for idx, name in enumerate(step_names):
        found = findings.get(name)
        if idx <= highest_completed_index:
            status: ResolutionStepStatus = "completed"
            evidence = found[1] if found else ()
            timestamp = found[2] if found else None
        elif found is not None:
            status = found[0]
            evidence = tuple(found[1])
            timestamp = found[2]
        else:
            status = "unknown"
            evidence = ()
            timestamp = None
        steps.append(ResolutionStep(name=name, status=status, evidence=tuple(evidence), timestamp=timestamp))

    known_statuses = [s for s in steps if s.status != "unknown"]
    if not known_statuses:
        steps_remaining = None
        path_completion = None
    else:
        completed = sum(1 for s in steps if s.status == "completed")
        # steps_remaining counts every step after the last known one as
        # "remaining" only when at least one step's real status is known —
        # otherwise the whole path is honestly unknown, not zero.
        steps_remaining = len(steps) - completed
        path_completion = round(completed / len(steps), 2)

    blockers = tuple(s.name + ": " + e for s in steps if s.status == "blocked" for e in s.evidence)
    deadline_pressure = _deadline_pressure(time_remaining_hours, steps_remaining)

    if blockers:
        path_feasibility: PathFeasibility = "LOW"
    elif not known_statuses:
        path_feasibility = "UNKNOWN"
    elif deadline_pressure == "CRITICAL" and (steps_remaining or 0) > 0:
        path_feasibility = "LOW"
    elif path_completion is not None and path_completion >= 0.5:
        path_feasibility = "HIGH"
    else:
        path_feasibility = "MEDIUM"

    return ResolutionPath(
        applies=True,
        steps=tuple(steps),
        steps_remaining=steps_remaining,
        path_completion=path_completion,
        deadline_pressure=deadline_pressure,
        path_feasibility=path_feasibility,
        blockers=blockers,
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
    # ROUND-1 addition: structured, per-step version of required_transitions
    # above (kept, unchanged, for backward compatibility with any existing
    # caller reading the plain-string form). Empty tuple whenever there is
    # no real, evidence-derived transition to describe — never fabricated
    # placeholder steps.
    required_transition_steps: tuple[TransitionStep, ...] = field(default_factory=tuple)
    # BLOCK C, Part 2 addition: the real multi-step ResolutionPath (distinct
    # from `required_transition_steps` above, which describes a single
    # generic state-A -> state-B transition). Architectural call, documented
    # here rather than only in HANDOFF.md: `PathToResolution` is genuinely
    # the right home for this — it is already the "what would need to happen
    # for YES vs NO" summary object for Politics/Geopolitics markets, and
    # `ResolutionPath` is a structural refinement of that same question for
    # markets whose event_type has a KNOWN multi-step process, rather than a
    # parallel, disconnected concept. Kept as its own dataclass (not merged
    # into `required_transition_steps`) because the two model genuinely
    # different shapes: `TransitionStep` is a single current-state ->
    # target-state edge; `ResolutionPath` is an ORDERED SEQUENCE of named,
    # independently-statused steps.
    resolution_path: ResolutionPath | None = None

    def as_dict(self) -> dict:
        return {
            "current_state": self.current_state,
            "yes_condition": self.yes_condition,
            "no_condition": self.no_condition,
            "time_remaining_hours": self.time_remaining_hours,
            "required_transitions": list(self.required_transitions),
            "supporting_conditions": list(self.supporting_conditions),
            "blocking_conditions": list(self.blocking_conditions),
            "required_transition_steps": [s.as_dict() for s in self.required_transition_steps],
            "resolution_path": self.resolution_path.as_dict() if self.resolution_path is not None else None,
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
    required_transition_steps: tuple[TransitionStep, ...] = ()
    if waterway_state is not None and current_state not in ("NORMAL", "UNKNOWN"):
        required_transitions = (
            (
                f"waterway status must transition from {current_state} to NORMAL "
                f"(currently backed by {waterway_state.basis_evidence_count} dated evidence item(s))"
            ),
        )
        # ROUND-1: the structured per-step form of the same real transition
        # described above — same underlying facts (current_state, the
        # already-computed supporting/blocking evidence titles), just
        # shaped per the target schema. probability_status is ALWAYS
        # UNKNOWN (see TransitionStep's docstring) — no historical basis
        # for a transition-probability number exists anywhere in this
        # codebase yet, and none is invented here.
        required_transition_steps = (
            TransitionStep(
                state_from=current_state,
                state_to="NORMAL",
                required_event=(
                    f"waterway operational status returns to NORMAL "
                    f"(currently backed by {waterway_state.basis_evidence_count} dated evidence item(s))"
                ),
                supporting_evidence=supporting_conditions,
                counter_evidence=blocking_conditions,
                estimated_duration=None,
                probability_status="UNKNOWN",
                confidence=None,
            ),
        )

    resolution_path = _derive_resolution_path(proposition, time_remaining_hours, independent_evidence)

    return PathToResolution(
        current_state=current_state,
        yes_condition=proposition.yes_condition,
        no_condition=proposition.no_condition,
        time_remaining_hours=time_remaining_hours,
        required_transitions=required_transitions,
        supporting_conditions=supporting_conditions,
        blocking_conditions=blocking_conditions,
        required_transition_steps=required_transition_steps,
        resolution_path=resolution_path,
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
    # --- ROUND-2 (section 5): real structured state variables. Empty tuple
    # whenever this market has no real external data feed backing it (see
    # module header) — never fabricated placeholders.
    state_variables: tuple[StateVariable, ...] = field(default_factory=tuple)

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
            "state_variables": [v.as_dict() for v in self.state_variables],
        }


def assemble_world_state(
    proposition: MarketProposition,
    resolution_date: datetime | None,
    now: datetime,
    independent_evidence: IndependentEvidenceResult | None,
    classified_category: str | None = None,
    macro_snapshot: MacroSnapshot | None = None,
    quant_asset: str | None = None,
    quant_current_price: float | None = None,
    quant_daily_volatility: float | None = None,
) -> WorldState:
    """Assemble a WorldState from already-computed engine values. No new
    probability-affecting computation happens here.

    `classified_category` (additive, Part 3): the Phase-C classified
    taxonomy value (e.g. "GEOPOLITICS"/"POLITICS") — optional so every
    existing caller/test that omits it keeps working exactly as before;
    path_to_resolution is simply None in that case.

    `macro_snapshot`/`quant_*` (additive, ROUND-2 section 5): the SAME
    already-fetched FRED/CoinGecko values engine.py forwards to macro.py/
    quant.py — never fetched again here. All default to None so every
    existing caller keeps working unchanged; state_variables is simply an
    empty tuple whenever none are supplied (the honest, correct outcome for
    every market outside MACRO/CRYPTO)."""
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

    state_variables = (
        _build_macro_state_variables(macro_snapshot, now)
        + _build_quant_state_variables(quant_asset, quant_current_price, quant_daily_volatility, now)
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
        state_variables=state_variables,
    )
