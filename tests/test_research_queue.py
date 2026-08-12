"""Real, deterministic tests for the Research Queue priority scoring."""

from __future__ import annotations

from polymarketpulse.research_queue import MarketSignal, build_research_queue, compute_priority


def _signal(**overrides) -> MarketSignal:
    base = {
        "market_id": "m1",
        "question": "Test market?",
        "category": "POLITICS",
        "event_type": "office_departure",
        "market_probability": 0.1,
        "model_hypothesis_probability": None,
        "time_remaining_hours": 24 * 30,
        "critical_gap_count": 0,
        "high_gap_count": 0,
        "has_source_coverage": True,
    }
    base.update(overrides)
    return MarketSignal(**base)


def test_high_divergence_thin_evidence_outranks_no_signal_market() -> None:
    """A market with a large, unverified model-vs-market gap must rank
    above a market with no real signal at all — this is the core research
    priority this project cares about: 'is this possible edge real?'"""
    edge_candidate = _signal(
        market_id="edge",
        market_probability=0.10,
        model_hypothesis_probability=0.55,
    )
    flat_market = _signal(market_id="flat", market_probability=0.5, model_hypothesis_probability=None)

    queue = build_research_queue([flat_market, edge_candidate])
    assert queue[0].market_id == "edge"
    assert queue[1].market_id == "flat"
    assert any("Divergenz" in r for r in queue[0].reasons)


def test_no_source_coverage_deprioritizes_but_does_not_zero() -> None:
    covered = _signal(market_id="covered", model_hypothesis_probability=0.4, market_probability=0.1,
                       has_source_coverage=True)
    uncovered = _signal(market_id="uncovered", model_hypothesis_probability=0.4, market_probability=0.1,
                         has_source_coverage=False)

    covered_entry = compute_priority(covered)
    uncovered_entry = compute_priority(uncovered)

    assert uncovered_entry.priority_score < covered_entry.priority_score
    assert uncovered_entry.priority_score > 0  # deprioritized, not eliminated


def test_no_divergence_no_gaps_no_deadline_pressure_scores_near_zero() -> None:
    dead_market = _signal(
        market_probability=0.5,
        model_hypothesis_probability=None,
        time_remaining_hours=24 * 365,
        critical_gap_count=0,
        high_gap_count=0,
    )
    entry = compute_priority(dead_market)
    assert entry.priority_score < 5.0


def test_critical_data_gaps_raise_priority() -> None:
    no_gaps = _signal(market_id="clean", critical_gap_count=0, high_gap_count=0)
    many_gaps = _signal(market_id="gappy", critical_gap_count=3, high_gap_count=2)

    queue = build_research_queue([no_gaps, many_gaps])
    assert queue[0].market_id == "gappy"


def test_urgent_deadline_raises_priority_over_distant_deadline() -> None:
    soon = _signal(market_id="soon", time_remaining_hours=48)
    distant = _signal(market_id="distant", time_remaining_hours=24 * 300)

    queue = build_research_queue([soon, distant])
    assert queue[0].market_id == "soon"


def test_limit_truncates_queue() -> None:
    signals = [_signal(market_id=f"m{i}", model_hypothesis_probability=0.1 * i, market_probability=0.05)
               for i in range(10)]
    queue = build_research_queue(signals, limit=3)
    assert len(queue) == 3
