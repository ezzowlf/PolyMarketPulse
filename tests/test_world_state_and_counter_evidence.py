"""Tests for this round's two additions:

1. World State (prediction/world_state.py) — a real, reachable
   yes_condition/no_condition/time-remaining summary on PredictionResult,
   assembled from already-computed engine fields (steering point 9/21).
2. Counter-evidence detection (claims.detect_claim_contradictions) —
   connecting the previously-scaffolded `claim_counter_evidence` table
   (schema existed, `Storage.save_counter_evidence` existed, but nothing
   ever called it) to a REAL structural contradiction signal: two claims
   about the same subject/event_type with opposite directions.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from polymarketpulse.claims import ClaimGroup, detect_claim_contradictions
from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.prediction.world_state import assemble_world_state
from polymarketpulse.storage import Storage

# ---------------------------------------------------------------------
# World State: unit tests for assemble_world_state()
# ---------------------------------------------------------------------


def test_world_state_exposes_yes_no_condition_and_time_remaining() -> None:
    proposition = parse_market_proposition(
        "Will the ceasefire agreement be confirmed by December 31?", None
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    resolution_date = now + timedelta(hours=72)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=resolution_date, now=now, independent_evidence=None,
    )
    assert ws.yes_condition == proposition.yes_condition
    assert ws.no_condition == proposition.no_condition
    assert ws.yes_condition  # non-empty real string, not a placeholder
    assert ws.no_condition
    assert ws.time_remaining_hours == pytest.approx(72.0)
    assert ws.counter_evidence_count == 0
    assert ws.claim_status_counts == {}


def test_world_state_time_remaining_none_when_no_deadline_known() -> None:
    proposition = parse_market_proposition("Will BTC be above $100000?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC), independent_evidence=None,
    )
    # Honest None, not a fabricated 0 or negative number.
    assert ws.time_remaining_hours is None


def test_world_state_deadline_falls_back_to_real_resolution_date_when_text_parse_fails() -> None:
    """Real integration bug fix: proposition.deadline is only ever a
    regex-parsed date TEXT extracted from the question/resolution_text --
    semantics.py's parse_market_proposition never receives the market's
    real structured deadline/end_date columns at all. A question like
    "Clarity Act ... in 2026?" has no specific parseable date, so
    proposition.deadline stays honestly None even though a real
    resolution_date (the actual markets.end_date column) was already
    fetched by the caller. world_state.deadline must fall back to that
    real value rather than staying None just because the free-text
    parser found nothing."""
    proposition = parse_market_proposition(
        "Clarity Act (H.R.3633) signed into law in 2026?", None
    )
    assert proposition.deadline is None  # confirms the real parser limitation this test guards against
    now = datetime(2026, 8, 16, tzinfo=UTC)
    resolution_date = datetime(2027, 1, 1, 5, 0, tzinfo=UTC)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=resolution_date, now=now, independent_evidence=None,
    )
    assert ws.deadline == resolution_date.isoformat()


def test_world_state_deadline_prefers_real_parsed_text_when_available() -> None:
    """When the free-text parser DID find a real date, that stays
    authoritative -- the resolution_date fallback only fires when the
    parser found nothing at all."""
    proposition = parse_market_proposition(
        "Will the ceasefire agreement be confirmed by December 31?", None
    )
    assert proposition.deadline is not None
    now = datetime(2026, 1, 1, tzinfo=UTC)
    resolution_date = now + timedelta(hours=72)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=resolution_date, now=now, independent_evidence=None,
    )
    assert ws.deadline == proposition.deadline


def test_prediction_result_exposes_world_state_reachably() -> None:
    # Integration: compute_prediction() must attach a real WorldState object
    # to every PredictionResult, not leave the "what must happen for YES/NO"
    # answer computed-and-discarded inside engine.py as before this round.
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE markets (market_id TEXT PRIMARY KEY, provider TEXT, provider_market_id TEXT, category TEXT);
        CREATE TABLE market_resolutions (provider TEXT, provider_market_id TEXT, status TEXT, winning_outcome TEXT);
        """
    )
    conn.execute(
        "INSERT INTO markets VALUES ('ws-m1', 'polymarket', 'ws-m1', 'esports')"
    )
    conn.commit()
    result = compute_prediction(
        conn, "ws-m1", "polymarket", "ws-m1", "esports", 0.5, 50000, 90, 0, None, True
    )
    assert result.world_state is not None
    assert isinstance(result.world_state.yes_condition, str) and result.world_state.yes_condition
    assert isinstance(result.world_state.no_condition, str) and result.world_state.no_condition
    d = result.as_dict()
    assert "world_state" in d
    assert d["world_state"]["yes_condition"] == result.world_state.yes_condition


# ---------------------------------------------------------------------
# Counter-evidence: unit tests for detect_claim_contradictions()
# ---------------------------------------------------------------------


def _claim_group(claim_id: str, subject: str, event_type: str, direction: str) -> ClaimGroup:
    from polymarketpulse.claims import Claim

    canonical = Claim(
        claim_id=claim_id, subject=subject, predicate="p", object=None, speaker=None,
        source_id="src", source_url=None, timestamp=None, event_type=event_type, direction=direction,
    )
    return ClaimGroup(
        claim_id=claim_id, canonical_claim=canonical, republishing_sources=(),
        independent_sources=1, confirmation_count=1, verification_status="SINGLE_SOURCE",
    )


def test_detects_real_contradiction_same_subject_event_type_opposite_direction() -> None:
    a = _claim_group("c_a", "Iran", "ceasefire", "positive")
    b = _claim_group("c_b", "Iran", "ceasefire", "negative")
    updated, pairs = detect_claim_contradictions([a, b])
    assert pairs == (("c_a", "c_b"),)
    statuses = {g.claim_id: g.verification_status for g in updated}
    assert statuses["c_a"] == "DISPUTED"
    assert statuses["c_b"] == "DISPUTED"


def test_no_contradiction_when_subjects_differ() -> None:
    a = _claim_group("c_a", "Iran", "ceasefire", "positive")
    b = _claim_group("c_b", "Israel", "ceasefire", "negative")
    updated, pairs = detect_claim_contradictions([a, b])
    assert pairs == ()
    assert all(g.verification_status == "SINGLE_SOURCE" for g in updated)


def test_no_contradiction_when_event_types_differ() -> None:
    a = _claim_group("c_a", "Iran", "ceasefire", "positive")
    b = _claim_group("c_b", "Iran", "war_escalation", "negative")
    _updated, pairs = detect_claim_contradictions([a, b])
    assert pairs == ()


def test_no_contradiction_when_direction_same() -> None:
    # Two confirming claims (both positive) must NOT be flagged — this is
    # the "ordinary agreement" case, not a contradiction.
    a = _claim_group("c_a", "Iran", "ceasefire", "positive")
    b = _claim_group("c_b", "Iran", "ceasefire", "positive")
    _updated, pairs = detect_claim_contradictions([a, b])
    assert pairs == ()


def test_no_contradiction_when_direction_neutral() -> None:
    # Neutral direction is not a real yes/no signal — must never be treated
    # as "opposite" of anything (absence of a real signal is not itself a
    # contradiction).
    a = _claim_group("c_a", "Iran", "ceasefire", "neutral")
    b = _claim_group("c_b", "Iran", "ceasefire", "negative")
    _updated, pairs = detect_claim_contradictions([a, b])
    assert pairs == ()


# ---------------------------------------------------------------------
# Counter-evidence: real end-to-end persistence via compute_independent_evidence
# ---------------------------------------------------------------------


@pytest.fixture
def storage(tmp_path) -> Storage:
    s = Storage(tmp_path / "test_counter_evidence.db")
    yield s
    s.close()


def _link_news(storage: Storage, market: Market, title: str, source: str, source_url: str, hours_ago: float) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("ceasefire",), confidence=0.6,
    )
    storage.save_news_market_link(row_id, link)


def test_real_contradicting_claims_produce_recorded_counter_evidence(storage: Storage) -> None:
    # Realistic fixture: one article reports the ceasefire holding, a
    # second reports it collapsing — the same underlying subject/event
    # ("Ceasefire" / event_type "ceasefire") with genuinely opposite
    # directions (verified against extract_event/extract_claim_from_event
    # directly: 'Ceasefire confirmed...' -> direction=positive,
    # 'Ceasefire talks collapse, fighting resumes' -> direction=negative,
    # both event_type='ceasefire', both subject='Ceasefire').
    market = Market(
        provider="polymarket", provider_market_id="counter-ev-1", condition_id="",
        question="Will the ceasefire agreement be confirmed?", slug="counter-ev-1",
    )
    _link_news(
        storage, market, "Ceasefire confirmed by both sides, agreement signed",
        "reuters", "https://reuters.com/a", hours_ago=1,
    )
    _link_news(
        storage, market, "Ceasefire talks collapse, fighting resumes",
        "bbc", "https://bbc.com/b", hours_ago=2,
    )

    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="counter-ev-1",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )

    assert result.counter_evidence_count >= 1
    assert result.claim_status_counts.get("DISPUTED", 0) >= 1

    # Real row(s) actually persisted into claim_counter_evidence — not just
    # a number reported without a backing record.
    rows = storage.connection.execute("SELECT claim_id, contradicts_claim_id FROM claim_counter_evidence").fetchall()
    assert len(rows) >= 1


def test_two_agreeing_claims_produce_zero_counter_evidence_not_a_fabricated_positive(storage: Storage) -> None:
    # Two claims that both confirm the SAME direction must not be flagged —
    # absence of contradiction is the honest, common case and must read as
    # zero, never as a fabricated signal.
    market = Market(
        provider="polymarket", provider_market_id="counter-ev-2", condition_id="",
        question="Will the ceasefire agreement be confirmed?", slug="counter-ev-2",
    )
    _link_news(
        storage, market, "Ceasefire confirmed by both sides, agreement signed",
        "reuters", "https://reuters.com/a", hours_ago=1,
    )
    _link_news(
        storage, market, "Officials confirm ceasefire agreement reached",
        "apnews", "https://apnews.com/b", hours_ago=3,
    )

    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="counter-ev-2",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )

    assert result.counter_evidence_count == 0
    rows = storage.connection.execute("SELECT * FROM claim_counter_evidence").fetchall()
    assert len(rows) == 0
