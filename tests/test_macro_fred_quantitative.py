"""Tests for macro.py's real FRED-derived quantitative rate-decision
fallback (added this round). Proves the SAME market question's macro
probability genuinely differs based on different real (or realistically-
mocked, per fred.py's honest-mocking convention) policy-rate/CPI/
unemployment inputs — this is the legitimate case where the model's own
purpose IS to react to real macro data (unlike the market-price-blindness
invariant elsewhere in the codebase, which is about NOT reacting to the
Polymarket price)."""

from __future__ import annotations

from datetime import date

from polymarketpulse.prediction.macro import analyze_macro
from polymarketpulse.providers.fred import MacroSnapshot

# Deliberately phrased WITHOUT any cut/hike/hold keyword macro.py's own
# _RATE_CUT_KEYWORDS/_RATE_HIKE_KEYWORDS/_RATE_HOLD_KEYWORDS sets recognize,
# and without any _UPCOMING_KEYWORDS phrase either — this is a case the
# text-keyword path genuinely cannot resolve (returns "insufficient_signal"),
# so the real FRED quantitative fallback is what actually produces a
# probability, not a coincidental keyword match.
QUESTION = "What will the Federal Reserve decide at its September 2026 meeting?"


def _snapshot(cpi_yoy: float, cpi_yoy_prior: float, unemployment_rate: float, unemployment_rate_prior: float) -> MacroSnapshot:
    return MacroSnapshot(
        policy_rate=4.00,
        policy_rate_as_of=date(2026, 7, 1),
        cpi_yoy=cpi_yoy,
        cpi_yoy_prior=cpi_yoy_prior,
        unemployment_rate=unemployment_rate,
        unemployment_rate_prior=unemployment_rate_prior,
        as_of_date=date(2026, 8, 1),
        next_fomc_meeting_date=date(2026, 9, 16),
    )


def test_text_alone_is_still_used_when_confirmed_or_reported() -> None:
    # A market with unambiguous already-reported text keeps using the
    # keyword path — the FRED fallback must not override real text signal.
    result = analyze_macro(
        text="Fed decreases interest rates by 25 bps, confirmed rate cut announced",
        event_type="rate_cut",
        proposition_status="CLEAR",
        macro_snapshot=_snapshot(cpi_yoy=2.0, cpi_yoy_prior=3.0, unemployment_rate=5.0, unemployment_rate_prior=4.0),
    )
    assert result.available
    assert "fred_policy_rate" not in result.inputs_used


def test_upcoming_meeting_falls_back_to_quantitative_signal() -> None:
    result = analyze_macro(
        text=QUESTION,
        event_type="rate_cut",
        proposition_status="CLEAR",
        macro_snapshot=_snapshot(cpi_yoy=2.0, cpi_yoy_prior=3.0, unemployment_rate=5.0, unemployment_rate_prior=4.0),
    )
    assert result.available
    assert result.probability is not None
    assert "fred_policy_rate" in result.inputs_used
    assert "fred_cpi_yoy_trend" in result.inputs_used
    assert "fred_unemployment_trend" in result.inputs_used


def test_no_macro_snapshot_stays_unavailable() -> None:
    # Real environment-limitation case: FRED unreachable -> None snapshot ->
    # must NOT fabricate a probability.
    result = analyze_macro(
        text=QUESTION, event_type="rate_cut", proposition_status="CLEAR", macro_snapshot=None,
    )
    assert result.available is False
    assert result.probability is None
    # Must be distinguishable from "no evidence exists" — this is a real
    # source-fetch failure (FRED/BLS unreachable), not an absence of
    # relevant evidence in the world.
    assert result.data_source_status == "SOURCE_FETCH_FAILED"
    assert "SOURCE_FETCH_FAILED" in result.reason


def test_no_evidence_and_no_snapshot_need_reports_no_evidence_not_fetch_failure() -> None:
    # Event types outside the quantitative fallback (e.g. monetary_policy)
    # have no FRED/BLS signal to fail to fetch in the first place — this
    # must report NO_EVIDENCE, not a fabricated SOURCE_FETCH_FAILED.
    result = analyze_macro(
        text="Some unrelated statement.", event_type="monetary_policy",
        proposition_status="CLEAR", macro_snapshot=None,
    )
    assert result.available is False
    assert result.data_source_status == "NO_EVIDENCE"


def test_probability_genuinely_reacts_to_different_real_macro_inputs() -> None:
    # Cooling inflation + rising unemployment => real cut pressure.
    cut_favoring = analyze_macro(
        text=QUESTION, event_type="rate_cut", proposition_status="CLEAR",
        macro_snapshot=_snapshot(cpi_yoy=2.0, cpi_yoy_prior=3.5, unemployment_rate=5.5, unemployment_rate_prior=4.0),
    )
    # Heating inflation + falling unemployment => real hike pressure, i.e.
    # LESS reason to expect a cut at this same meeting.
    hike_favoring = analyze_macro(
        text=QUESTION, event_type="rate_cut", proposition_status="CLEAR",
        macro_snapshot=_snapshot(cpi_yoy=4.5, cpi_yoy_prior=3.0, unemployment_rate=3.5, unemployment_rate_prior=4.5),
    )
    assert cut_favoring.available and hike_favoring.available
    assert cut_favoring.probability > hike_favoring.probability


def test_rate_hike_and_rate_hold_use_matching_quantitative_key() -> None:
    snapshot = _snapshot(cpi_yoy=4.5, cpi_yoy_prior=3.0, unemployment_rate=3.5, unemployment_rate_prior=4.5)
    hike_result = analyze_macro(
        text="Will the Fed increase interest rates by 25 bps after the September 2026 meeting?",
        event_type="rate_hike", proposition_status="CLEAR", macro_snapshot=snapshot,
    )
    hold_result = analyze_macro(
        text="Will the Fed hold rates steady at the September 2026 meeting with no scheduled change?",
        event_type="rate_hold", proposition_status="CLEAR", macro_snapshot=snapshot,
    )
    assert hike_result.available
    assert hold_result.available
    # Heating inflation + falling unemployment strongly favors hike over hold here.
    assert hike_result.probability > hold_result.probability
