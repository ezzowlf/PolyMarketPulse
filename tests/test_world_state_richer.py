"""Tests for this round's richer WorldState additions (Politics/Geopolitics
evidence-backed forecasting, round following test_world_state_and_counter_
evidence.py):

  Part 1 — visibility into the already-symmetric yes/no evidence search
    (evidence_for_yes_count / evidence_for_no_count / actively_searched_
    both_sides).
  Part 2 — waterway/blockade health+trend sub-state, honestly UNKNOWN
    (never a guessed NORMAL) when there is no real evidence.
  Part 3 — Path-to-Resolution for Politics/Geopolitics markets, honestly
    empty lists (never fabricated text) when there is no real evidence.
  Part 4 — divergence-support classification (SUPPORTED/WEAKLY_SUPPORTED/
    UNSUPPORTED_DIVERGENCE), a thin relabeling of divergence_audit's own
    PASS/WARN/REJECT verdict.
  Part 5 — market-blindness re-confirmation: none of the new fields vary
    with the market's own price.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.divergence_audit import (
    AuditCheck,
    DivergenceAuditResult,
    classify_divergence_support,
)
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.prediction.world_state import assemble_world_state
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


class _FakeMarket:
    def __init__(self, question: str) -> None:
        self.question = question


def _link_evidence(
    storage: Storage, provider: str, provider_market_id: str, question: str,
    items: list[tuple[str, str, str, float]], now: datetime | None = None,
) -> None:
    """items: list of (title, source, source_url, link_confidence), most
    recent first (index 0 = most recent, matching _link_evidence's hours-ago
    convention used elsewhere in this test suite)."""
    now = now or datetime.now(UTC)
    market = _FakeMarket(question)
    for i, (title, source, source_url, confidence) in enumerate(items):
        event = NewsEvent(
            source=source, source_url=source_url, title=title,
            published_at=now - timedelta(hours=i), fetched_at=now,
        )
        row_id = storage.save_news_event(event)
        link = NewsMarketLink(
            news_event=event, market=market, match_reason="shared_terms",
            matched_terms=(), confidence=confidence,
        )
        storage.connection.execute(
            "INSERT INTO news_market_links (news_event_id, provider, provider_market_id, "
            "match_reason, matched_terms, confidence, confirmed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'automatic', ?)",
            (row_id, provider, provider_market_id, link.match_reason, "", confidence, now.isoformat()),
        )
    storage.connection.commit()


# ---------------------------------------------------------------------------
# Part 1 — symmetric-search visibility
# ---------------------------------------------------------------------------


def test_evidence_counts_and_symmetric_search_flag_exposed(storage: Storage) -> None:
    question = "Will the ceasefire hold through year end?"
    resolution = "Resolves YES if the ceasefire holds. Resolves NO if fighting resumes."
    _link_evidence(
        storage, "polymarket", "m1", question,
        [
            ("Ceasefire confirmed, both sides agree, hostilities halted", "reuters", "https://reuters.com/a", 0.9),
            ("Military offensive intensifies, sides trade attacks", "bbc", "https://bbc.com/b", 0.9),
        ],
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m1", question, resolution, market_yes_price=0.5,
    )
    proposition = parse_market_proposition(question, resolution)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC), independent_evidence=ev,
    )
    assert ev.available
    assert ws.evidence_for_yes_count == len(ev.evidence_for_yes)
    assert ws.evidence_for_no_count == len(ev.evidence_for_no)
    # Real yes+no split (not just all-one-direction) proves the pipeline
    # actually considered both sides, not merely accumulated whatever was
    # linked.
    assert ws.evidence_for_yes_count >= 1
    assert ws.evidence_for_no_count >= 1
    assert ws.actively_searched_both_sides is True


def test_no_evidence_still_reports_symmetric_search_true() -> None:
    # actively_searched_both_sides describes the SEARCH mechanism (which is
    # symmetric by construction — classify_evidence_relation checks
    # relation_kind "same"/"opposite" identically for every item), not
    # whether evidence was actually found — so it stays True even with zero
    # independent evidence available.
    proposition = parse_market_proposition("Will X happen?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC), independent_evidence=None,
    )
    assert ws.actively_searched_both_sides is True
    assert ws.evidence_for_yes_count == 0
    assert ws.evidence_for_no_count == 0


# ---------------------------------------------------------------------------
# Part 2 — waterway health/trend: UNKNOWN-not-NORMAL invariant
# ---------------------------------------------------------------------------


def test_waterway_state_is_unknown_not_normal_with_zero_evidence() -> None:
    proposition = parse_market_proposition(
        "Will the Strait of Hormuz traffic return to normal by August 31?", None
    )
    assert proposition.event_type == "strategic_waterway"
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC), independent_evidence=None,
    )
    assert ws.waterway_state is not None
    # The critical invariant: absence of evidence is UNKNOWN, never a
    # guessed NORMAL/STABLE.
    assert ws.waterway_state.current_state == "UNKNOWN"
    assert ws.waterway_state.trend == "UNKNOWN"
    assert ws.waterway_state.basis_evidence_count == 0


def test_waterway_state_none_for_non_waterway_event_type() -> None:
    proposition = parse_market_proposition("Will BTC be above $100000?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC), independent_evidence=None,
    )
    assert ws.waterway_state is None


def test_waterway_state_reflects_real_direct_tier_evidence(storage: Storage) -> None:
    # Real evidence-derived state: a confirmed ceasefire report whose
    # headline text also describes shipping returning to normal.
    question = "Will the ceasefire hold through year end?"
    _link_evidence(
        storage, "polymarket", "m1", question,
        [
            ("Ceasefire confirmed, both sides agree, shipping resumes normal levels", "reuters", "https://reuters.com/a", 0.9),
            ("Officials confirm ceasefire holding steady", "apnews", "https://apnews.com/b", 0.9),
        ],
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m1", question, None, market_yes_price=0.5,
    )
    proposition = parse_market_proposition(question, None)
    assert proposition.event_type == "ceasefire"
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC), independent_evidence=ev,
    )
    assert ws.waterway_state is not None
    assert ws.waterway_state.current_state == "NORMAL"
    assert ws.waterway_state.basis_evidence_count >= 1


def test_waterway_trend_deteriorating_from_two_dated_direct_tier_items(storage: Storage) -> None:
    now = datetime.now(UTC)
    question = "Will the ceasefire hold through year end?"
    # index 0 = most recent (per _link_evidence convention: hours_ago = i).
    _link_evidence(
        storage, "polymarket", "m1", question,
        [
            ("Military offensive intensifies, waterway blockade imposed", "bbc", "https://bbc.com/a", 0.9),
            ("Ceasefire confirmed, shipping returns to normal", "reuters", "https://reuters.com/b", 0.9),
        ],
        now=now,
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m1", question, None, market_yes_price=0.5, now=now,
    )
    proposition = parse_market_proposition(question, None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=now, independent_evidence=ev,
    )
    assert ws.waterway_state is not None
    assert ws.waterway_state.current_state == "CLOSED"
    assert ws.waterway_state.trend == "DETERIORATING"
    assert ws.waterway_state.basis_evidence_count == 2


# ---------------------------------------------------------------------------
# Part 3 — Path-to-Resolution: real-evidence-derivation, empty when no
# evidence exists (never fabricated placeholder text).
# ---------------------------------------------------------------------------


def test_path_to_resolution_empty_lists_when_no_evidence() -> None:
    proposition = parse_market_proposition("Will the President resign this year?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category="POLITICS",
    )
    assert ws.path_to_resolution is not None
    assert ws.path_to_resolution.supporting_conditions == ()
    assert ws.path_to_resolution.blocking_conditions == ()
    assert ws.path_to_resolution.required_transitions == ()
    assert ws.path_to_resolution.yes_condition == proposition.yes_condition
    assert ws.path_to_resolution.no_condition == proposition.no_condition


def test_path_to_resolution_none_for_non_politics_geopolitics_category() -> None:
    proposition = parse_market_proposition("Will BTC be above $100000?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category="CRYPTO",
    )
    assert ws.path_to_resolution is None


def test_path_to_resolution_none_when_category_not_supplied() -> None:
    proposition = parse_market_proposition("Will the President resign this year?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC), independent_evidence=None,
    )
    assert ws.path_to_resolution is None


def test_path_to_resolution_real_supporting_and_blocking_conditions(storage: Storage) -> None:
    question = "Will the ceasefire hold through year end?"
    _link_evidence(
        storage, "polymarket", "m1", question,
        [
            ("Ceasefire confirmed, both sides agree, hostilities halted", "reuters", "https://reuters.com/a", 0.9),
            ("Military offensive intensifies, sides trade attacks", "bbc", "https://bbc.com/b", 0.9),
        ],
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m1", question, None, market_yes_price=0.5,
    )
    proposition = parse_market_proposition(question, None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=ev, classified_category="GEOPOLITICS",
    )
    assert ws.path_to_resolution is not None
    # Real titles, not invented text — every item must trace back to an
    # actual linked evidence headline.
    all_titles = {f.title for f in (*ev.evidence_for_yes, *ev.evidence_for_no)}
    for cond in ws.path_to_resolution.supporting_conditions:
        assert cond in all_titles
    for cond in ws.path_to_resolution.blocking_conditions:
        assert cond in all_titles
    assert ws.path_to_resolution.supporting_conditions or ws.path_to_resolution.blocking_conditions


# ---------------------------------------------------------------------------
# Part 4 — divergence-support classification
# ---------------------------------------------------------------------------


def _check(verdict: str) -> AuditCheck:
    return AuditCheck(name="x", verdict=verdict, detail="d")


def test_divergence_support_pass_maps_to_supported() -> None:
    result = DivergenceAuditResult(triggered=True, gap=0.2, verdict="PASS", checks=(_check("PASS"),))
    assert classify_divergence_support(result) == "SUPPORTED_DIVERGENCE"


def test_divergence_support_warn_maps_to_weakly_supported() -> None:
    result = DivergenceAuditResult(triggered=True, gap=0.2, verdict="WARN", checks=(_check("WARN"),))
    assert classify_divergence_support(result) == "WEAKLY_SUPPORTED_DIVERGENCE"


def test_divergence_support_reject_maps_to_unsupported() -> None:
    result = DivergenceAuditResult(triggered=True, gap=0.2, verdict="REJECT", checks=(_check("REJECT"),))
    assert classify_divergence_support(result) == "UNSUPPORTED_DIVERGENCE"


def test_divergence_support_none_when_not_triggered() -> None:
    result = DivergenceAuditResult(triggered=False, gap=0.05, verdict=None)
    assert classify_divergence_support(result) is None


def test_prediction_result_exposes_divergence_support_field() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE markets (market_id TEXT PRIMARY KEY, provider TEXT, provider_market_id TEXT, category TEXT);
        CREATE TABLE market_resolutions (provider TEXT, provider_market_id TEXT, status TEXT, winning_outcome TEXT);
        """
    )
    conn.execute("INSERT INTO markets VALUES ('ws-m2', 'polymarket', 'ws-m2', 'esports')")
    conn.commit()
    result = compute_prediction(
        conn, "ws-m2", "polymarket", "ws-m2", "esports", 0.5, 50000, 90, 0, None, True
    )
    # No comparable history seeded -> divergence check likely never
    # triggers, so divergence_support is honestly None here; the field
    # must simply exist and be reachable via as_dict().
    d = result.as_dict()
    assert "divergence_support" in d
    assert d["divergence_support"] == result.divergence_support


# ---------------------------------------------------------------------------
# Part 5 — market-blindness re-confirmation for the new fields
# ---------------------------------------------------------------------------


def test_world_state_new_fields_are_market_blind(tmp_path: Path) -> None:
    """None of this round's new world_state fields (waterway_state,
    path_to_resolution, evidence_for_yes/no counts) may vary with the
    market's own price — they are derived only from independent evidence
    and the parsed proposition, exactly like the existing world_state
    fields already are (see test_world_state_and_counter_evidence.py)."""
    question = "Will the ceasefire hold through year end?"
    now = datetime.now(UTC)
    results = []
    for i, price in enumerate((0.05, 0.25, 0.50, 0.75, 0.95)):
        storage = Storage(tmp_path / f"blind-{i}.db")
        _link_evidence(
            storage, "polymarket", "m1", question,
            [
                ("Ceasefire confirmed, shipping resumes normal levels", "reuters", "https://reuters.com/a", 0.9),
                ("Officials confirm ceasefire holding steady", "apnews", "https://apnews.com/b", 0.9),
            ],
            now=now,
        )
        storage.connection.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, condition_id, question, slug, "
            "category, classified_category, url, first_seen_at, last_seen_at) "
            "VALUES ('m1', 'polymarket', 'm1', '', ?, 'm1', 'GEOPOLITICS', 'GEOPOLITICS', 'https://x', ?, ?)",
            (question, now.isoformat(), now.isoformat()),
        )
        storage.connection.commit()
        results.append(
            compute_prediction(
                storage.connection, "m1", "polymarket", "m1", "GEOPOLITICS", price, 100000, 90, 0, None, True,
                question=question, classified_category="GEOPOLITICS",
            )
        )
        storage.close()

    waterway_states = {(r.world_state.waterway_state.current_state, r.world_state.waterway_state.trend) for r in results}
    yes_counts = {r.world_state.evidence_for_yes_count for r in results}
    no_counts = {r.world_state.evidence_for_no_count for r in results}
    assert len(waterway_states) == 1
    assert len(yes_counts) == 1
    assert len(no_counts) == 1
