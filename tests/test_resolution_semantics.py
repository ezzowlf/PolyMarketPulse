"""ROUND-1 tests — Part 2 (Resolution Engine, resolution_semantics.py)."""

from __future__ import annotations

from polymarketpulse.prediction.confidence import compute_data_quality_composite
from polymarketpulse.prediction.resolution_semantics import extract_resolution_semantics
from polymarketpulse.prediction.semantics import parse_market_proposition


def test_clear_resolution_with_explicit_yes_no_clause_and_authority() -> None:
    # Subject-leading phrasing (not "Will ...") so the proposition itself
    # is CLEAR (see semantics.py's naive subject extractor, which excludes
    # sentence-initial "Will") — isolates the resolution-clause/authority
    # extraction being tested here from the separate subject-detection gap.
    question = "Federal Reserve to decrease interest rates by 25 bps after the September 2026 meeting?"
    resolution_text = (
        "Resolves YES if the Fed cuts rates by 25bps. Resolves NO if it does not, "
        "as determined by Federal Reserve."
    )
    rs = extract_resolution_semantics(question, resolution_text)
    assert rs.measurement == "official_rate_announcement"
    assert rs.required_source == "Federal Reserve"
    assert "resolution_source_inferred_from_domain" not in rs.ambiguities
    assert "no_yes_no_clause_in_resolution_text" not in rs.ambiguities
    assert rs.confidence > 0.5


def test_ambiguous_resolution_with_no_text_and_no_event_type() -> None:
    rs = extract_resolution_semantics("Will something unusual happen next week?", None)
    assert rs.measurement is None
    assert "no_resolution_text_supplied" in rs.ambiguities
    assert "no_event_type_detected" in rs.ambiguities
    assert "no_resolution_source_identified" in rs.ambiguities
    assert rs.confidence < 0.3


def test_domain_inferred_source_flagged_as_inferred_not_explicit() -> None:
    question = "Will the price of Bitcoin be above $80,000 on August 7?"
    rs = extract_resolution_semantics(question, None)
    assert rs.measurement == "spot_price"
    assert rs.required_source is not None
    assert "resolution_source_inferred_from_domain" in rs.ambiguities


def test_deadline_semantics_unclear_flagged_when_deadline_present_but_ambiguous() -> None:
    # A deadline phrase that matches neither the "by <date>" nor the
    # "on/at/as of <date>" pattern precisely enough — constructed so
    # `deadline` stays None too (no deadline at all is a different, cleaner
    # case than "we found a deadline word but couldn't tell which kind").
    # Real example: this codebase's own deadline detector only recognizes
    # "by"/"on/at/as of" phrasing, so anything else (e.g. "before the end of
    # the year") legitimately has no deadline extracted at all.
    prop = parse_market_proposition("Will BTC be above $80,000?", None)
    assert prop.deadline is None
    rs = extract_resolution_semantics("Will BTC be above $80,000?", None, prop)
    assert rs.deadline is None
    assert "deadline_semantics_unclear_by_vs_at" not in rs.ambiguities


def test_low_resolution_confidence_caps_data_quality_composite_dimension() -> None:
    """Verifies the honest-signal-caps-maturity wiring: a market with an
    ambiguous resolution (low ResolutionSemantics.confidence) must produce
    a strictly lower resolution_semantics_clarity dimension score than a
    market with a clear one, and that dimension must actually be included
    in the data_quality_composite output (not computed and discarded)."""
    clear_prop = parse_market_proposition(
        "Federal Reserve to decrease interest rates by 25 bps after the September 2026 meeting?", None
    )
    clear_rs = extract_resolution_semantics(
        "Federal Reserve to decrease interest rates by 25 bps after the September 2026 meeting?",
        "Resolves YES if the Fed cuts rates. Resolves NO if it does not, as determined by Federal Reserve.",
        clear_prop,
    )
    ambiguous_prop = parse_market_proposition("Will something unusual happen next week?", None)
    ambiguous_rs = extract_resolution_semantics("Will something unusual happen next week?", None, ambiguous_prop)

    assert clear_rs.confidence > ambiguous_rs.confidence

    dq_clear = compute_data_quality_composite(
        proposition=clear_prop, history_uncertainty=None, comparable_sample_size=0,
        independent_evidence=None, specialized_estimates=[], eligible_specialized_models=(),
        aktualitaet=50.0, resolution_semantics=clear_rs,
    )
    dq_ambiguous = compute_data_quality_composite(
        proposition=ambiguous_prop, history_uncertainty=None, comparable_sample_size=0,
        independent_evidence=None, specialized_estimates=[], eligible_specialized_models=(),
        aktualitaet=50.0, resolution_semantics=ambiguous_rs,
    )
    clear_dim = next(d for d in dq_clear.dimensions if d.name == "resolution_semantics_clarity")
    ambiguous_dim = next(d for d in dq_ambiguous.dimensions if d.name == "resolution_semantics_clarity")
    assert clear_dim.available and ambiguous_dim.available
    assert clear_dim.normalized_score > ambiguous_dim.normalized_score
    # The overall composite score must also reflect this, not just the
    # per-dimension breakdown.
    assert dq_clear.score >= dq_ambiguous.score


def test_resolution_semantics_dimension_unavailable_when_not_supplied() -> None:
    """Backward compatibility: existing callers that don't pass
    resolution_semantics at all must keep working exactly as before —
    the new dimension is reported unavailable, not fabricated."""
    prop = parse_market_proposition("Will BTC be above $80,000?", None)
    dq = compute_data_quality_composite(
        proposition=prop, history_uncertainty=None, comparable_sample_size=0,
        independent_evidence=None, specialized_estimates=[], eligible_specialized_models=(),
        aktualitaet=50.0,
    )
    dim = next(d for d in dq.dimensions if d.name == "resolution_semantics_clarity")
    assert dim.available is False
    assert dim.normalized_score is None


def test_as_dict_shape() -> None:
    rs = extract_resolution_semantics("Will BTC be above $80,000 on August 7?", None)
    d = rs.as_dict()
    for key in ("yes_condition", "no_condition", "deadline", "measurement", "threshold", "required_source", "ambiguities", "confidence"):
        assert key in d
