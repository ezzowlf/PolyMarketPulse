"""Phase 7.1 — Data Acquisition Core: DataGap.gap_type is a pure, real
relabeling of the existing DataGapCategory taxonomy, never a second
independent classification."""

from __future__ import annotations

from polymarketpulse.data_gaps import DataGap, GapPriority


def _gap(category: str) -> DataGap:
    return DataGap(
        category=category, severity="HIGH", description="x", priority=GapPriority.HIGH,
        impact_on_confidence=0.1, recommended_sources=(),
    )


def test_every_real_category_maps_to_a_real_gap_type() -> None:
    expected = {
        "NEWS_PRIMARY": "MISSING_PRIMARY_SOURCE",
        "NEWS_SECONDARY": "MISSING_INDEPENDENT_CONFIRMATION",
        "STRUCTURED_DATA": "MISSING_STRUCTURED_DATA",
        "EVENT_GRAPH": "MISSING_MODEL_INPUT",
        "HISTORICAL_COMPARABLE": "MISSING_MODEL_INPUT",
        "TIME_HORIZON": "MISSING_TIMING_DATA",
        "STATE_ENGINE": "MISSING_RESOLUTION_DATA",
        "MARKET_HISTORY": "MISSING_TIMING_DATA",
        "GEOGRAPHIC_DATA": "MISSING_MODEL_INPUT",
        "ECONOMIC_DATA": "MISSING_MODEL_INPUT",
        "RESOLUTION_PATH": "MISSING_RESOLUTION_DATA",
    }
    for category, gap_type in expected.items():
        assert _gap(category).gap_type == gap_type


def test_gap_type_appears_in_as_dict() -> None:
    gap = _gap("NEWS_PRIMARY")
    assert gap.as_dict()["gap_type"] == "MISSING_PRIMARY_SOURCE"
