"""Phase E — Structured World State: one compact, per-market summary
composed ENTIRELY from values other modules have already computed (world_state.py's
WorldState/ResolutionPath, data_gaps.py's DataGapReport). This module performs
no new probability-affecting computation and fetches no new data — its only
job is to stop five different consumers (forecast explanation, scenario
engine, research queue, GPT explanation, market detail UI) from each
re-deriving their own slightly-different notion of "what do we currently
know about this market", which is exactly the kind of drift the project
owner explicitly flagged ("keine fünf unterschiedlichen Wahrheiten in fünf
Modulen")."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..data_gaps import DataGapReport
from .world_state import PathToResolution, ResolutionPath, WorldState


@dataclass(frozen=True)
class StructuredWorldState:
    confirmed_facts: tuple[str, ...] = field(default_factory=tuple)
    disputed_facts: tuple[str, ...] = field(default_factory=tuple)
    current_state: str | None = None
    completed_steps: tuple[str, ...] = field(default_factory=tuple)
    open_steps: tuple[str, ...] = field(default_factory=tuple)
    blockers: tuple[str, ...] = field(default_factory=tuple)
    # Phase F (Next Event Engine) is what will ever populate this with real
    # entries -- honestly empty until that phase exists, never a
    # placeholder.
    expected_events: tuple[str, ...] = field(default_factory=tuple)
    open_questions: tuple[str, ...] = field(default_factory=tuple)
    data_gaps: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "confirmed_facts": list(self.confirmed_facts),
            "disputed_facts": list(self.disputed_facts),
            "current_state": self.current_state,
            "completed_steps": list(self.completed_steps),
            "open_steps": list(self.open_steps),
            "blockers": list(self.blockers),
            "expected_events": list(self.expected_events),
            "open_questions": list(self.open_questions),
            "data_gaps": list(self.data_gaps),
        }


def _current_state_summary(
    world_state: WorldState, resolution_path: ResolutionPath | None, path_to_resolution: PathToResolution | None,
) -> str | None:
    """A single human-readable "where things stand" sentence, preferring
    the most specific real signal available: an active resolution path's
    own current-step description, then the generic path-to-resolution
    state, then waterway health, then a fallback built from real evidence
    counts. Never invents a state when nothing real is known."""
    if resolution_path is not None and resolution_path.applies:
        completed = [s for s in resolution_path.steps if s.status == "completed"]
        if completed:
            return f"Zuletzt bestätigt: {completed[-1].name} ({resolution_path.template_name})."
        return f"Resolution-Pfad ({resolution_path.template_name}) noch ohne bestätigten Schritt."
    if path_to_resolution is not None:
        return path_to_resolution.current_state
    if world_state.waterway_state is not None:
        return f"Waterway-Status: {world_state.waterway_state.current_state}."
    if world_state.evidence_for_yes_count or world_state.evidence_for_no_count:
        return (
            f"{world_state.evidence_for_yes_count} Belege für YES, "
            f"{world_state.evidence_for_no_count} Belege für NO erfasst."
        )
    return None


def assemble_structured_world_state(
    world_state: WorldState,
    resolution_path: ResolutionPath | None,
    data_gap_report: DataGapReport | None,
) -> StructuredWorldState:
    confirmed_facts: list[str] = []
    if world_state.most_recent_evidence_headline:
        confirmed_facts.append(world_state.most_recent_evidence_headline)
    if resolution_path is not None:
        for step in resolution_path.steps:
            if step.status == "completed":
                confirmed_facts.extend(step.evidence)

    disputed_facts: list[str] = []
    if world_state.counter_evidence_count > 0:
        disputed_facts.append(
            f"{world_state.counter_evidence_count} widersprüchliche Behauptung(en) erkannt, "
            "noch nicht aufgelöst."
        )

    completed_steps: list[str] = []
    open_steps: list[str] = []
    blockers: tuple[str, ...] = ()
    if resolution_path is not None and resolution_path.applies:
        for step in resolution_path.steps:
            if step.status == "completed":
                completed_steps.append(step.name)
            else:
                open_steps.append(step.name)
        blockers = resolution_path.blockers

    open_questions: list[str] = []
    data_gaps: list[str] = []
    if data_gap_report is not None:
        data_gaps = [gap.description for gap in data_gap_report.gaps]
        open_questions = [gap.description for gap in data_gap_report.gaps if gap.severity in ("CRITICAL", "HIGH")]

    return StructuredWorldState(
        confirmed_facts=tuple(confirmed_facts),
        disputed_facts=tuple(disputed_facts),
        current_state=_current_state_summary(world_state, resolution_path, world_state.path_to_resolution),
        completed_steps=tuple(completed_steps),
        open_steps=tuple(open_steps),
        blockers=tuple(blockers),
        open_questions=tuple(open_questions),
        data_gaps=tuple(data_gaps),
    )
