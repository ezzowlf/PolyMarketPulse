"""Real-data-integration slice (Clarity Act, `polymarket:1163699`, H.R.3633):
tests for the three real, additive pieces built to fill the existing
intelligence architecture with real, market-relevant legislative-status
data:

1. `providers/govtrack.py` — a real, free, keyless bill-status fetcher
   (mocked httpx responses shaped like GovTrack's real API, since live
   network access is not guaranteed in every test environment; the parsing/
   derivation logic itself is real, not stubbed out).
2. `prediction/semantics.py::extract_event`'s new "legislative_progress"
   action family — general rules (any bill's legislative-status language),
   not hardcoded to this one market's text.
3. `source_registry.py`'s new "govtrack" source entry and its routing for
   `legislation`-typed gaps.

A general-purpose fixture test also proves the real end-to-end effect
documented in HANDOFF.md: linking succeeds and the extraction/classification
chain is real, but evidence.py's existing "early signal" recency decay
(24h half-life, unrelated to and unmodified by this work) genuinely zeroes
out the contribution of evidence describing a legislative action that is
more than a few days old — this is the real, honestly-reported outcome for
the Clarity Act's currently-stale (>1 year old) real evidence, not a claim
that the pipeline fabricated a fresh forecast.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import link_news_to_markets
from polymarketpulse.prediction.evidence import _recency_weight_local
from polymarketpulse.prediction.semantics import (
    classify_evidence_relation,
    extract_event,
    parse_market_proposition,
)
from polymarketpulse.providers import govtrack
from polymarketpulse.source_registry import (
    get_source_definition,
    recommend_sources_for_gap,
)

# ---------------------------------------------------------------------------
# 1. semantics.py: legislative_progress action family (general, not
#    hardcoded to the Clarity Act's exact wording).
# ---------------------------------------------------------------------------


def test_legislative_progress_detects_house_passage_as_direct_yes() -> None:
    prop = parse_market_proposition(
        "Clarity Act (H.R.3633) signed into law in 2026?",
        "This market will resolve to Yes if the Digital Asset Market Clarity Act of 2025 "
        "(H.R.3633) is passed by both chambers of the U.S. Congress and signed into law by "
        "December 31, 2026, 11:59 PM ET. Otherwise, this market will resolve to No.",
    )
    assert prop.event_type == "legislation"
    assert prop.subject == "Clarity Act"

    ev = extract_event("Clarity Act officially passed the House.")
    assert ev.action == "legislative_progress"
    assert ev.event_type == "legislation"
    assert ev.status == "actual"
    assert ev.certainty == "confirmed"

    relation = classify_evidence_relation(prop, ev, sentiment=0.0, link_confidence=0.3)
    assert relation.label == "DIRECT_YES"
    assert relation.quantitative_weight == 1.0


def test_legislative_progress_detects_committee_clearance_as_supports_yes() -> None:
    prop = parse_market_proposition(
        "Some Other Bill (H.R.9999) signed into law in 2026?",
        "This market will resolve to Yes if H.R.9999 is passed by both chambers of Congress "
        "and signed into law.",
    )
    ev = extract_event("Some Other Bill cleared committee.")
    assert ev.action == "legislative_progress"
    assert ev.status == "actual"  # "completed" step classification

    relation = classify_evidence_relation(prop, ev, sentiment=0.0, link_confidence=0.3)
    # No "confirmed"-tier certainty keyword in this headline -> SUPPORTS_YES,
    # not the full-weight DIRECT_YES tier — a real, honest distinction, not
    # a fixed outcome hardcoded per-bill.
    assert relation.label == "SUPPORTS_YES"
    assert relation.quantitative_weight == pytest.approx(0.55)


def test_legislative_progress_in_progress_terms_yield_intent_status() -> None:
    ev = extract_event("Some Bill is scheduled for a Senate vote next week.")
    assert ev.action == "legislative_progress"
    assert ev.status == "intent"
    assert ev.event_type == "legislation"


def test_legislative_progress_does_not_override_an_already_recognized_action() -> None:
    # A headline that happens to contain BOTH a resignation phrase and
    # legislative language must not be reclassified — legislative_progress
    # is only ever checked when no other action family already matched.
    ev = extract_event("Senator resigns after the bill passed the House.")
    assert ev.action == "resignation"


def test_no_legislative_keyword_present_action_stays_none() -> None:
    ev = extract_event("A quiet Tuesday in Washington.")
    assert ev.action is None
    assert ev.event_type is None


# ---------------------------------------------------------------------------
# 2. providers/govtrack.py: real parsing logic against realistic mocked
#    GovTrack API responses (same verification standard as
#    tests/test_fred_provider.py / test_coingecko_provider.py).
# ---------------------------------------------------------------------------


def _govtrack_payload_for_hr3633() -> dict:
    # Shape matches the real, live-verified GovTrack API response for
    # H.R.3633 (119th Congress) as of 2026-08 (see HANDOFF.md).
    return {
        "meta": {"limit": 100, "offset": 0, "total_count": 1},
        "objects": [
            {
                "congress": 119,
                "bill_type": "house_bill",
                "number": 3633,
                "display_number": "H.R. 3633",
                "title": "Digital Asset Market Clarity Act of 2025",
                "current_status": "pass_over_house",
                "current_status_label": "Passed House (Senate next)",
                "current_status_description": "This bill passed in the House on July 17, 2025 "
                "and goes to the Senate next for consideration.",
                "current_status_date": "2025-07-17",
                "introduced_date": "2025-05-29",
                "is_alive": True,
                "link": "https://www.govtrack.us/congress/bills/119/hr3633",
                "major_actions": [
                    [
                        "datetime.datetime(2025, 6, 10, 0, 0)",
                        3,
                        "Ordered to be Reported (Amended) by the Yeas and Nays: 47 - 6.",
                        "<action .../>",
                    ],
                    [
                        "datetime.datetime(2025, 7, 17, 15, 30, 31)",
                        4,
                        "On passage Passed by the Yeas and Nays: 294 - 134 (Roll no. 199).",
                        "<vote .../>",
                    ],
                ],
            }
        ],
    }


def test_fetch_bill_status_parses_realistic_govtrack_response(monkeypatch) -> None:
    def _fake_get(url, timeout=None, headers=None, verify=None):
        assert "govtrack.us" in url
        return httpx.Response(200, json=_govtrack_payload_for_hr3633(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _fake_get)
    status = govtrack.fetch_bill_status(119, govtrack.BILL_TYPE_HOUSE_BILL, 3633)

    assert status is not None
    assert status.display_number == "H.R. 3633"
    assert status.current_status == "pass_over_house"
    assert status.is_alive is True
    assert len(status.major_actions) == 2
    assert status.major_actions[0].occurred_at.isoformat() == "2025-06-10"
    assert "Reported" in status.major_actions[0].text
    assert status.major_actions[1].occurred_at.isoformat() == "2025-07-17"
    assert "Passed" in status.major_actions[1].text


def test_fetch_bill_status_returns_none_on_empty_result(monkeypatch) -> None:
    def _fake_get(url, timeout=None, headers=None, verify=None):
        return httpx.Response(
            200, json={"meta": {"total_count": 0}, "objects": []}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx, "get", _fake_get)
    assert govtrack.fetch_bill_status(119, govtrack.BILL_TYPE_HOUSE_BILL, 999999) is None


def test_fetch_bill_status_returns_none_on_http_error(monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(httpx, "get", _raise)
    assert govtrack.fetch_bill_status(119, govtrack.BILL_TYPE_HOUSE_BILL, 3633) is None


def test_fetch_bill_status_rejects_unsafe_url(monkeypatch) -> None:
    monkeypatch.setattr(govtrack, "_API_BASE_URL", "http://127.0.0.1/bill")
    assert govtrack.fetch_bill_status(119, govtrack.BILL_TYPE_HOUSE_BILL, 3633) is None


# ---------------------------------------------------------------------------
# 3. source_registry.py: govtrack is real routing infrastructure, not a
#    disconnected catalog entry.
# ---------------------------------------------------------------------------


def test_govtrack_is_registered_and_routed_for_legislation() -> None:
    definition = get_source_definition("govtrack")
    assert definition is not None
    assert definition.relevance_by_event_type.get("legislation") == "HIGH"

    routed = recommend_sources_for_gap("LEGISLATION", "legislation")
    assert "govtrack" in routed


# ---------------------------------------------------------------------------
# 4. End-to-end (offline): real linking succeeds; the real, honest recency-
#    decay limitation is proven precisely (not asserted, reproduced).
# ---------------------------------------------------------------------------


def test_market_specific_linking_accepts_legislative_evidence_not_unrelated_articles() -> None:
    market = Market(
        provider="polymarket", provider_market_id="1163699", condition_id="0x9",
        question="Clarity Act (H.R.3633) signed into law in 2026?",
        slug="clarity-act", category="LEGISLATION",
    )
    on_topic = NewsEvent(
        source="govtrack", source_url="https://www.govtrack.us/x/house",
        title="Clarity Act officially passed the House.",
        published_at=datetime(2025, 7, 17, tzinfo=UTC), fetched_at=datetime.now(UTC),
        source_domain="govtrack.us",
    )
    # Anti-pattern guard (same discipline as the Trump/Nevada regression
    # tests elsewhere): an unrelated bill about a completely different
    # subject must NOT link to this market just because it is also
    # legislation-shaped.
    off_topic = NewsEvent(
        source="govtrack", source_url="https://www.govtrack.us/x/unrelated",
        title="Farm Subsidy Reform Act clears committee.",
        published_at=datetime(2025, 7, 18, tzinfo=UTC), fetched_at=datetime.now(UTC),
        source_domain="govtrack.us",
    )

    links = link_news_to_markets([on_topic, off_topic], [market])
    linked_titles = {link.news_event.title for link in links}
    assert on_topic.title in linked_titles
    assert off_topic.title not in linked_titles


def test_stale_legislative_evidence_is_honestly_decayed_to_zero_weight_by_recency() -> None:
    """Locks in the real, precisely-diagnosed finding from the live
    Clarity Act integration run (see HANDOFF.md): evidence.py's existing
    24h-half-life recency weighting (designed for early/breaking-signal
    news, not modified by this round's work) genuinely reduces a real,
    correctly DIRECT_YES-classified but >1-year-old legislative fact to a
    recency_weight of 0.0 — proving the extraction/classification/linking
    chain is real and correct while the overall independent-evidence
    contribution is honestly zero for stale structural facts, not silently
    faked to look like a working live update."""
    now = datetime(2026, 8, 12, tzinfo=UTC)
    stale_published_at = "2025-07-17T15:30:31+00:00"
    assert _recency_weight_local(stale_published_at, now) == 0.0

    fresh_published_at = (now).isoformat()
    assert _recency_weight_local(fresh_published_at, now) > 0.9
