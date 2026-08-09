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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .evidence import IndependentEvidenceResult
    from .semantics import MarketProposition


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
        }


def assemble_world_state(
    proposition: MarketProposition,
    resolution_date: datetime | None,
    now: datetime,
    independent_evidence: IndependentEvidenceResult | None,
) -> WorldState:
    """Assemble a WorldState from already-computed engine values. No new
    probability-affecting computation happens here."""
    time_remaining_hours: float | None = None
    if resolution_date is not None:
        time_remaining_hours = (resolution_date - now).total_seconds() / 3600.0

    most_recent_headline: str | None = None
    most_recent_published_at: str | None = None
    counter_evidence_count = 0
    claim_status_counts: dict = {}
    if independent_evidence is not None:
        counter_evidence_count = independent_evidence.counter_evidence_count
        claim_status_counts = dict(independent_evidence.claim_status_counts)
        candidates = [
            f for f in (independent_evidence.evidence_for_yes + independent_evidence.evidence_for_no)
            if f.published_at
        ]
        if candidates:
            latest = max(candidates, key=lambda f: f.published_at)
            most_recent_headline = latest.title
            most_recent_published_at = latest.published_at

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
    )
