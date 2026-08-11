"""Block D — Part 3 (concrete Data Gap Engine descriptions) and Part 4
(deterministic Change Triggers), unit-tested directly against the real
Block C ResolutionPath structure (no LLM, no network)."""

from __future__ import annotations

from polymarketpulse.data_gaps import calculate_data_gaps
from polymarketpulse.prediction.change_triggers import compute_change_triggers
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.prediction.world_state import _derive_resolution_path


def _legislation_proposition():
    return parse_market_proposition(
        "Will the Clarity Act pass both chambers of Congress and be signed into law by December 31?", None
    )


def test_resolution_path_gap_names_the_specific_unknown_step() -> None:
    """Part 3: a legislation market with no dated evidence at all should
    produce a RESOLUTION_PATH gap naming the FIRST concrete unknown step
    ("introduced"), not a generic 'zu wenig Daten' string."""
    proposition = _legislation_proposition()
    assert proposition.event_type == "legislation"
    path = _derive_resolution_path(proposition, time_remaining_hours=24 * 60, independent_evidence=None)
    assert path.applies is True
    assert all(s.status == "unknown" for s in path.steps)

    report = calculate_data_gaps(
        market_id="m1", question=proposition.subject or "clarity act",
        market_category="LEGISLATION", event_type="legislation",
        source_health=None, historical_comparables_count=0,
        time_horizon_compatible=None, has_structured_data=False,
        has_event_relations=False, resolution_path=path,
    )
    path_gaps = [g for g in report.gaps if g.category == "RESOLUTION_PATH"]
    assert len(path_gaps) == 1
    gap = path_gaps[0]
    # Concrete, not generic: names the real step, both German label and the
    # literal internal step name.
    assert "Einbringung des Gesetzentwurfs" in gap.description
    assert "introduced" in gap.description
    assert "unbekannt" in gap.description
    assert gap.severity in ("MEDIUM", "HIGH")


def test_no_resolution_path_gap_for_non_multistep_market() -> None:
    """The overwhelming-majority case: a simple binary market has no known
    multi-step structure at all (`applies=False`), so no RESOLUTION_PATH gap
    is fabricated — honest absence, not a forced generic entry."""
    proposition = parse_market_proposition("Will Bitcoin be above $100,000 on December 31?", None)
    path = _derive_resolution_path(proposition, time_remaining_hours=100, independent_evidence=None)
    assert path.applies is False

    report = calculate_data_gaps(
        market_id="m2", question="btc", market_category="CRYPTO", event_type=proposition.event_type,
        source_health=None, historical_comparables_count=20, time_horizon_compatible=True,
        has_structured_data=True, has_event_relations=True, resolution_path=path,
    )
    assert not [g for g in report.gaps if g.category == "RESOLUTION_PATH"]


def test_data_gaps_backward_compatible_without_resolution_path() -> None:
    """resolution_path is optional/additive — existing callers that don't
    pass it keep working unchanged (no RESOLUTION_PATH gap, no crash)."""
    report = calculate_data_gaps(
        market_id="m3", question="x", market_category="POLITICS", event_type=None,
        source_health=None, historical_comparables_count=1, time_horizon_compatible=None,
        has_structured_data=False, has_event_relations=False,
    )
    assert not [g for g in report.gaps if g.category == "RESOLUTION_PATH"]


# --- Part 4: Change Triggers -------------------------------------------------


class _FakePathToResolution:
    def __init__(self, resolution_path):
        self.resolution_path = resolution_path


class _FakeWorldState:
    def __init__(self, resolution_path=None, counter_evidence_count=0):
        self.path_to_resolution = _FakePathToResolution(resolution_path) if resolution_path is not None else None
        self.counter_evidence_count = counter_evidence_count


def test_change_trigger_names_real_open_step() -> None:
    proposition = _legislation_proposition()
    path = _derive_resolution_path(proposition, time_remaining_hours=24 * 60, independent_evidence=None)
    ws = _FakeWorldState(resolution_path=path)
    triggers = compute_change_triggers(world_state=ws, data_gaps=None, divergence_audit=None)
    assert triggers, "expected at least one real trigger for an open legislation step"
    assert any("Terminierung/Durchführung der Senatsabstimmung" not in t for t in triggers)
    assert any("Einbringung des Gesetzentwurfs" in t for t in triggers)


def test_change_trigger_honestly_empty_for_simple_market_with_no_gaps() -> None:
    """Most binary/simple markets have no real derivable trigger — empty
    tuple is the honest default, not forced filler text."""
    ws = _FakeWorldState(resolution_path=None, counter_evidence_count=0)
    triggers = compute_change_triggers(world_state=ws, data_gaps=None, divergence_audit=None)
    assert triggers == ()


def test_change_trigger_from_contradiction_count() -> None:
    ws = _FakeWorldState(resolution_path=None, counter_evidence_count=2)
    triggers = compute_change_triggers(world_state=ws, data_gaps=None, divergence_audit=None)
    assert any("Widerspr" in t for t in triggers)


def test_change_trigger_from_rejected_divergence() -> None:
    class _FakeAudit:
        verdict = "REJECT"

    triggers = compute_change_triggers(world_state=None, data_gaps=None, divergence_audit=_FakeAudit())
    assert any("unabhängige Bestätigung" in t for t in triggers)


def test_change_triggers_deduplicated() -> None:
    proposition = _legislation_proposition()
    path = _derive_resolution_path(proposition, time_remaining_hours=24 * 60, independent_evidence=None)
    ws = _FakeWorldState(resolution_path=path)

    class _FakeAudit:
        verdict = "REJECT"

    t1 = compute_change_triggers(world_state=ws, data_gaps=None, divergence_audit=_FakeAudit())
    t2 = compute_change_triggers(world_state=ws, data_gaps=None, divergence_audit=_FakeAudit())
    assert t1 == t2
    assert len(t1) == len(set(t1))
