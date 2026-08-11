"""Block D Part 4 — Change Triggers.

Deterministic "what would change our assessment" statements, generated
directly from already-real structured data this engine computes elsewhere
this same run: Block C's ResolutionPath (open/unknown resolution steps),
Block D Part 3's Data Gap Engine output (critical/high gaps), claims.py's
contradiction detection (surfaced via world_state's counter_evidence_count),
and the divergence/evidence state (divergence_audit's REJECT verdict).

This is explicitly NOT an LLM-generated or GPT-invented list — every string
below is built from a plain, literal template applied to a real field value
already present on the inputs. No network calls, no free text generation. A
future block may hand these candidate strings to GPT for more natural
phrasing (ai/service.py), but the underlying trigger *content* must be
traceable to a real structured value, which is what this module guarantees.

`empty tuple` is the honest default: most binary/simple markets (no known
multi-step resolution structure, no unresolved critical/high data gaps, no
detected contradictions, no rejected divergence) genuinely have no concrete,
derivable "this specific thing would change our mind" statement — forcing
generic filler text for those markets would violate the same
"no fabricated signal" principle the rest of this codebase follows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..data_gaps import DataGapReport
    from .divergence_audit import DivergenceAuditResult
    from .world_state import WorldState

_STEP_NAME_DE: dict[str, str] = {
    "introduced": "die Einbringung des Gesetzentwurfs",
    "committee": "den Abschluss der Ausschussphase",
    "house_vote": "die Abstimmung im Repräsentantenhaus",
    "senate_vote": "die offizielle Terminierung/Durchführung der Senatsabstimmung",
    "presidential_action": "die Unterzeichnung oder das Veto durch den Präsidenten",
}


def compute_change_triggers(
    world_state: WorldState | None,
    data_gaps: DataGapReport | None,
    divergence_audit: DivergenceAuditResult | None,
) -> tuple[str, ...]:
    """Pure function of already-computed engine outputs. Order: resolution-
    path steps first (most concrete/actionable), then unresolved
    critical/high data gaps not already covered by a resolution-path
    trigger, then contradiction/divergence state. Deduplicated, order-
    preserving."""
    triggers: list[str] = []

    # 1. Open resolution-path steps (Block C) — the single most concrete
    #    trigger this codebase can derive: a named, real next step.
    path = (
        world_state.path_to_resolution.resolution_path
        if world_state is not None and world_state.path_to_resolution is not None
        else None
    )
    if path is not None and path.applies:
        unknown_steps = [s for s in path.steps if s.status == "unknown"]
        if unknown_steps:
            next_step = unknown_steps[0]
            label = _STEP_NAME_DE.get(next_step.name, next_step.name)
            triggers.append(f"Forecast würde sich ändern bei: {label}.")
        for step in path.steps:
            if step.status == "blocked":
                label = _STEP_NAME_DE.get(step.name, step.name)
                triggers.append(f"Forecast würde sich ändern bei: Aufhebung der Blockade von {label}.")

    # 2. Unresolved critical/high data gaps (Block D Part 3), excluding
    #    RESOLUTION_PATH gaps (already represented by trigger 1 above, from
    #    the same real ResolutionPath object — avoids a duplicate trigger
    #    describing the same underlying fact twice).
    if data_gaps is not None:
        for gap in data_gaps.gaps:
            if gap.severity not in ("CRITICAL", "HIGH"):
                continue
            if gap.category == "RESOLUTION_PATH":
                continue
            if gap.category == "NEWS_PRIMARY":
                triggers.append(
                    "Forecast würde sich ändern bei: Veröffentlichung einer verifizierten Primärquelle "
                    "(offizielle Erklärung/Pressemitteilung)."
                )
            elif gap.category == "STRUCTURED_DATA":
                sources = ", ".join(gap.recommended_sources) if gap.recommended_sources else "einer strukturierten Datenquelle"
                triggers.append(f"Forecast würde sich ändern bei: Verfügbarkeit von {sources}.")
            elif gap.category == "HISTORICAL_COMPARABLE":
                triggers.append(
                    "Forecast würde sich ändern bei: Auftreten weiterer vergleichbarer historischer Fälle."
                )

    # 3. Contradiction state (claims.py's detect_claim_contradictions,
    #    surfaced via world_state.counter_evidence_count — a real,
    #    non-zero count means at least one genuine structural contradiction
    #    between claim groups was detected and persisted this run).
    if world_state is not None and world_state.counter_evidence_count and world_state.counter_evidence_count > 0:
        triggers.append(
            "Forecast würde sich ändern bei: Auflösung der widersprüchlichen Berichte "
            f"({world_state.counter_evidence_count} erkannte(r) Widerspruch/Widersprüche)."
        )

    # 4. Divergence/evidence state — a REJECTed divergence audit is itself
    #    a real, named reason the forecast is currently unpublished.
    if divergence_audit is not None and divergence_audit.verdict == "REJECT":
        triggers.append(
            "Forecast würde sich ändern bei: unabhängige Bestätigung (>=2 Quellen, direkte Evidenz), "
            "die die aktuelle Modellabweichung von der Marktpreis rechtfertigt."
        )

    # Deduplicate while preserving first-seen order (e.g. two blocked steps
    # naming the same label, or a gap already implied by the path trigger).
    seen: set[str] = set()
    deduped: list[str] = []
    for t in triggers:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return tuple(deduped)
