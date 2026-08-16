"""Phase 7.7/7.8 — Critical/Optional Input Contracts + Data Coverage +
Next Best Research Action.

Not a new architecture: this module only reads fields other real modules
already compute (model_diagnostics from fed_policy.py, structured_world_state
from Phase E, next_event from Phase F, event_clock from Phase G, resolution_semantics
from the Resolution Engine, independent_evidence). Its job is to answer,
per already-supported archetype, "which of the real inputs this specific
product mode needs are actually available right now" -- never a second,
independently-invented notion of what a market needs.

Per explicit instruction: input contracts are derived from what the real
Champion model / real template actually uses, not invented. The Fed
contract's one CRITICAL input is exactly fed_policy.py's own
feature_list=["previous_fomc_action"] -- no generic "2 news sources" rule
substituted in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Criticality = Literal["CRITICAL", "OPTIONAL"]


@dataclass(frozen=True)
class InputRequirement:
    input_key: str
    human_label: str
    criticality: Criticality
    required_for: Literal["NUMERIC_FORECAST", "STRUCTURED_OUTLOOK", "BOTH"]


# Real, curated per-archetype contracts. Deliberately narrow: only the
# archetypes this codebase actually has a real model/template for
# (MACRO_POLICY / LEGISLATION / GEOPOLITICS) get a contract at all --
# everything else (sports, unsupported categories) honestly has none.
INPUT_CONTRACTS: dict[str, tuple[InputRequirement, ...]] = {
    "MACRO_POLICY": (
        # The ONE real feature fed_policy.py's champion model trains and
        # infers on (see fed_policy.py's `feature_list`). Nothing else is
        # critical to the model's own inference, however useful it might
        # be for explanation -- inventing a second "critical" input here
        # would misrepresent what the validated model actually needs.
        InputRequirement("prior_fomc_action", "letzte offizielle FOMC-Entscheidung", "CRITICAL", "NUMERIC_FORECAST"),
    ),
    "LEGISLATION": (
        InputRequirement("bill_identity", "eindeutige Gesetzesvorlage (Bill-Nummer)", "CRITICAL", "BOTH"),
        InputRequirement("official_current_stage", "aktueller offizieller Verfahrensstand", "CRITICAL", "BOTH"),
        InputRequirement("resolution_deadline", "Auflösungsfrist des Markts", "CRITICAL", "BOTH"),
        InputRequirement("next_required_step", "nächster notwendiger Verfahrensschritt", "CRITICAL", "STRUCTURED_OUTLOOK"),
        InputRequirement("schedule_timing", "konkreter Terminplan (z.B. Senatstermin)", "OPTIONAL", "STRUCTURED_OUTLOOK"),
    ),
    "GEOPOLITICS": (
        InputRequirement("resolution_semantics", "eindeutige Auflösungsregel", "CRITICAL", "BOTH"),
        InputRequirement("primary_measurement_source", "primäre strukturierte Messquelle", "CRITICAL", "BOTH"),
        InputRequirement("current_observation", "aktueller beobachteter Zustand", "CRITICAL", "BOTH"),
        InputRequirement("freshness", "Aktualität der zugrunde liegenden Beobachtung", "CRITICAL", "BOTH"),
        InputRequirement("independent_confirmation", "zweite unabhängige Bestätigung", "OPTIONAL", "STRUCTURED_OUTLOOK"),
    ),
}


@dataclass(frozen=True)
class DataCoverage:
    archetype: str | None
    critical_total: int
    critical_available: int
    critical_fresh: int
    critical_stale: int
    critical_failed: int
    optional_total: int
    optional_available: int
    coverage_ratio: float | None
    blocking_inputs: tuple[str, ...] = field(default_factory=tuple)
    next_missing_input: str | None = None

    def as_dict(self) -> dict:
        return {
            "archetype": self.archetype,
            "critical_total": self.critical_total,
            "critical_available": self.critical_available,
            "critical_fresh": self.critical_fresh,
            "critical_stale": self.critical_stale,
            "critical_failed": self.critical_failed,
            "optional_total": self.optional_total,
            "optional_available": self.optional_available,
            "coverage_ratio": self.coverage_ratio,
            "blocking_inputs": list(self.blocking_inputs),
            "next_missing_input": self.next_missing_input,
        }


def _contract_archetype(prediction) -> str | None:
    """Which real contract applies -- MACRO_POLICY when the Fed archetype
    was actually routed to, LEGISLATION/GEOPOLITICS from the real
    resolution_path template_name (Phase B), never guessed from the
    question text a second time."""
    if getattr(prediction, "forecast_archetype", None) == "MACRO_POLICY":
        return "MACRO_POLICY"
    world_state = getattr(prediction, "world_state", None)
    path_to_resolution = getattr(world_state, "path_to_resolution", None) if world_state else None
    resolution_path = getattr(path_to_resolution, "resolution_path", None) if path_to_resolution else None
    template_name = getattr(resolution_path, "template_name", None) if resolution_path else None
    if template_name in INPUT_CONTRACTS:
        return template_name
    return None


def _input_availability(prediction, input_key: str) -> tuple[bool, bool]:
    """Returns (available, fresh) for one real input_key. `fresh` is only
    meaningful when `available` is True. Every check reads an
    already-computed real field -- no new fetch, no guess."""
    diagnostics = getattr(prediction, "model_diagnostics", None) or {}
    sws = getattr(prediction, "structured_world_state", None)
    next_event = getattr(prediction, "next_event", None)
    event_clock = getattr(prediction, "event_clock", None)
    world_state = getattr(prediction, "world_state", None)
    resolution_semantics = getattr(prediction, "resolution_semantics", None)
    independent_evidence = getattr(prediction, "independent_evidence", None)

    if input_key == "prior_fomc_action":
        available = diagnostics.get("prior_action") is not None
        return available, available  # fed_policy.py rejects a stale prior action outright (SEMANTICS_UNCERTAIN/STALE)

    if input_key == "bill_identity":
        target = diagnostics.get("target") if isinstance(diagnostics, dict) else None
        available = bool(target) or (sws is not None and bool(sws.confirmed_facts or sws.completed_steps))
        return available, available

    if input_key == "official_current_stage":
        available = sws is not None and bool(sws.completed_steps)
        return available, available

    if input_key == "resolution_deadline":
        available = bool(getattr(event_clock, "deadline", None)) or bool(getattr(world_state, "deadline", None))
        return available, available

    if input_key == "next_required_step":
        available = next_event is not None and next_event.next_event_type is not None
        return available, available

    if input_key == "schedule_timing":
        # A real, concrete timing signal: either NextEvent's own
        # expected_time_window (Phase F) or EventClock's real
        # required_steps_remaining count -- never just "open_steps exist"
        # (which is redundant with next_required_step above).
        available = getattr(next_event, "expected_time_window", None) is not None or (
            getattr(event_clock, "required_steps_remaining", None) is not None
        )
        return available, available

    if input_key == "resolution_semantics":
        available = resolution_semantics is not None
        return available, available

    if input_key == "primary_measurement_source":
        available = independent_evidence is not None and independent_evidence.available
        return available, available

    if input_key == "current_observation":
        available = sws is not None and bool(sws.current_state) and sws.current_state != "UNKNOWN"
        return available, available

    if input_key == "freshness":
        available = bool(getattr(world_state, "most_recent_evidence_published_at", None))
        return available, available

    if input_key == "independent_confirmation":
        yes_count = getattr(world_state, "evidence_for_yes_count", 0) or 0
        no_count = getattr(world_state, "evidence_for_no_count", 0) or 0
        available = (yes_count + no_count) >= 2
        return available, available

    return False, False


def compute_data_coverage(prediction) -> DataCoverage:
    archetype = _contract_archetype(prediction)
    if archetype is None:
        return DataCoverage(
            archetype=None, critical_total=0, critical_available=0, critical_fresh=0,
            critical_stale=0, critical_failed=0, optional_total=0, optional_available=0,
            coverage_ratio=None, blocking_inputs=(), next_missing_input=None,
        )

    requirements = INPUT_CONTRACTS[archetype]
    critical_total = critical_available = critical_fresh = critical_stale = critical_failed = 0
    optional_total = optional_available = 0
    blocking: list[str] = []
    next_missing: str | None = None

    for req in requirements:
        available, fresh = _input_availability(prediction, req.input_key)
        if req.criticality == "CRITICAL":
            critical_total += 1
            if available:
                critical_available += 1
                if fresh:
                    critical_fresh += 1
                else:
                    critical_stale += 1
            else:
                critical_failed += 1
                blocking.append(req.human_label)
                if next_missing is None:
                    next_missing = req.human_label
        else:
            optional_total += 1
            if available:
                optional_available += 1

    coverage_ratio = round(critical_fresh / critical_total, 3) if critical_total > 0 else None

    return DataCoverage(
        archetype=archetype, critical_total=critical_total, critical_available=critical_available,
        critical_fresh=critical_fresh, critical_stale=critical_stale, critical_failed=critical_failed,
        optional_total=optional_total, optional_available=optional_available,
        coverage_ratio=coverage_ratio, blocking_inputs=tuple(blocking), next_missing_input=next_missing,
    )


# --- Phase 7.8: Next Best Research Action -----------------------------------

_PROVIDER_BY_INPUT: dict[str, str] = {
    "bill_identity": "govtrack", "official_current_stage": "govtrack",
    "next_required_step": "govtrack", "schedule_timing": "congress_gov",
    "resolution_semantics": "imf_portwatch", "primary_measurement_source": "imf_portwatch",
    "current_observation": "imf_portwatch", "freshness": "imf_portwatch",
    "independent_confirmation": "gdelt",
}


def derive_next_research_action(prediction, coverage: DataCoverage) -> dict:
    """One primary, human-readable next step per market, plus the
    machine-actionable detail underneath. Deterministic: the highest-
    criticality missing input wins; a market with full critical coverage
    or no archetype gets an honest "nothing to research" action rather
    than invented busywork."""
    if coverage.archetype is None:
        return {
            "action_type": "NONE", "target_information": None, "human_summary":
                "Kein numerisches oder strukturiertes Modell für diesen Markt verfügbar — keine automatische Recherche.",
            "reason": "NO_ARCHETYPE", "preferred_provider": None, "gap_key": None,
        }
    if coverage.next_missing_input is None:
        return {
            "action_type": "NONE", "target_information": None,
            "human_summary": "Alle kritischen Modelldaten sind vorhanden.",
            "reason": "COVERAGE_COMPLETE", "preferred_provider": None, "gap_key": None,
        }
    requirements = INPUT_CONTRACTS[coverage.archetype]
    missing_req = next(r for r in requirements if r.human_label == coverage.next_missing_input)
    provider = _PROVIDER_BY_INPUT.get(missing_req.input_key)
    provider_market_id = getattr(prediction, "market_id", None)
    return {
        "action_type": "FETCH",
        "target_information": missing_req.human_label,
        "human_summary": f"{missing_req.human_label.capitalize()} prüfen.",
        "reason": f"CRITICAL_INPUT_MISSING:{missing_req.input_key}",
        "preferred_provider": provider,
        "gap_key": f"input:{coverage.archetype}:{missing_req.input_key}",
        "provider_market_id": provider_market_id,
    }
