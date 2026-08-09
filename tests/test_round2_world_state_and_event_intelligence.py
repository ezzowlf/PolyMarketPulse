"""Round 2 (85-section brief, sections 5-6): World State 2.0 real structured
state variables + Event Intelligence dedup/confirmation-count wiring.

Part 1 — `StateVariable` / `WorldState.state_variables`:
  - populated with REAL fields for a MACRO/FRED market and a CRYPTO/
    CoinGecko market (reusing the already-fetched snapshot/price data, no
    new network calls)
  - honestly EMPTY for a GEOPOLITICS/POLITICS/SPORTS market with no real
    feed backing it — never a fabricated UNKNOWN placeholder.

Part 2 — claims.py's existing dedup (`group_claims_by_normalization`) audit:
  proves N syndicated articles describing the SAME underlying event produce
  a deduplicated confirmation_count (not N), and that this actually reaches
  `IndependentEvidenceResult.confirmation_count` (previously computed via
  claim-group persistence but silently discarded before reaching the real
  evidence-scoring confirmation_count — see evidence.py's fix this round).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.prediction.semantics import parse_market_proposition
from polymarketpulse.prediction.world_state import assemble_world_state
from polymarketpulse.providers.coingecko import PriceData
from polymarketpulse.providers.fred import MacroSnapshot
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
    """items: list of (title, source, source_url, link_confidence)."""
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
# Part 1 — StateVariable / state_variables
# ---------------------------------------------------------------------------


def _macro_snapshot() -> MacroSnapshot:
    return MacroSnapshot(
        policy_rate=4.25,
        policy_rate_as_of=date(2026, 7, 1),
        cpi_yoy=2.7,
        cpi_yoy_prior=3.1,
        unemployment_rate=4.3,
        unemployment_rate_prior=4.1,
        as_of_date=date(2026, 8, 1),
        next_fomc_meeting_date=date(2026, 9, 16),
    )


def test_macro_market_gets_real_populated_state_variables() -> None:
    proposition = parse_market_proposition(
        "Will the Fed cut interest rates at its September 2026 meeting?", None
    )
    assert proposition.event_type == "rate_cut"
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category="CENTRAL_BANKS",
        macro_snapshot=_macro_snapshot(),
    )
    by_name = {v.name: v for v in ws.state_variables}
    assert {"current_rate", "latest_cpi", "unemployment_rate", "next_meeting_date"} <= by_name.keys()

    rate = by_name["current_rate"]
    assert rate.value == 4.25
    assert rate.unit == "percent"
    assert rate.source == "fred"
    assert rate.source_type == "live_fetch"
    assert rate.verification_status == "provider_reported"
    assert rate.timestamp == "2026-07-01"
    assert rate.confidence > 0

    cpi = by_name["latest_cpi"]
    assert cpi.value == 2.7
    assert cpi.source == "fred"

    unemployment = by_name["unemployment_rate"]
    assert unemployment.value == 4.3

    next_meeting = by_name["next_meeting_date"]
    assert next_meeting.value == "2026-09-16"

    # None of this is a text summary — every field is a real typed value
    # traceable back to the MacroSnapshot fixture above.
    d = ws.as_dict()
    assert len(d["state_variables"]) == 4


def test_quant_market_gets_real_populated_state_variables() -> None:
    proposition = parse_market_proposition("Will Bitcoin be above $100,000 on December 31?", None)
    assert proposition.event_type == "price_above"
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category="CRYPTO",
        quant_asset="bitcoin", quant_current_price=97000.0, quant_daily_volatility=0.032,
    )
    by_name = {v.name: v for v in ws.state_variables}
    assert {"spot_price", "realized_volatility"} <= by_name.keys()

    spot = by_name["spot_price"]
    assert spot.value == 97000.0
    assert spot.unit == "usd"
    assert spot.source == "coingecko"
    assert spot.source_type == "live_fetch"
    assert spot.verification_status == "provider_reported"

    vol = by_name["realized_volatility"]
    assert vol.value == 0.032
    assert vol.source == "coingecko"

    # No fabricated "trend" variable — quant.py computes no such signal.
    assert "trend" not in by_name


def test_quant_market_empty_state_variables_when_price_unavailable() -> None:
    """Real, honest absence: no CoinGecko price data fetched this run (e.g.
    live fetch failed) must not produce placeholder state variables."""
    proposition = parse_market_proposition("Will Bitcoin be above $100,000 on December 31?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category="CRYPTO",
        quant_asset="bitcoin", quant_current_price=None, quant_daily_volatility=None,
    )
    assert ws.state_variables == ()


@pytest.mark.parametrize(
    "question,category",
    [
        ("Will Iran close the Strait of Hormuz by August 31?", "GEOPOLITICS"),
        ("Will Trump be out as President of the United States by August 31?", "POLITICS"),
        ("Will Team Alpha win their next match?", "SPORTS"),
    ],
)
def test_non_macro_non_crypto_markets_get_honestly_empty_state_variables(
    question: str, category: str,
) -> None:
    """No real external data feed exists for these domains in this
    codebase — state_variables must be an honestly empty tuple, never a
    fabricated UNKNOWN-valued placeholder (the project owner's explicit
    'UNKNOWN darf nicht als neutraler Wert missbraucht werden' rule)."""
    proposition = parse_market_proposition(question, None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None, classified_category=category,
        # Deliberately NOT passing macro_snapshot/quant_* — these domains
        # never fetch either in the real engine (see engine.py's gating on
        # event_type before either provider call).
    )
    assert ws.state_variables == ()


def test_backward_compatible_default_is_empty_tuple() -> None:
    """Every existing caller that omits the new params keeps working
    unchanged — state_variables defaults to empty, not None/error."""
    proposition = parse_market_proposition("Will X happen?", None)
    ws = assemble_world_state(
        proposition=proposition, resolution_date=None, now=datetime.now(UTC),
        independent_evidence=None,
    )
    assert ws.state_variables == ()
    assert ws.as_dict()["state_variables"] == []


def test_end_to_end_quant_market_state_variables_via_compute_prediction(
    storage: Storage, monkeypatch,
) -> None:
    """Full engine wiring, not just the assemble_world_state unit test
    above: a real compute_prediction() call on a quant-routed market must
    surface real state_variables on the result's world_state."""
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.resolve_coingecko_id", lambda asset: "bitcoin"
    )
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.fetch_price_and_volatility",
        lambda coingecko_id: PriceData(current_price=97000.0, daily_volatility=0.032, days_of_history=90),
    )
    question = "Will Bitcoin reach $40,000 by December 31, 2030?"
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "crypto", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text="Resolves YES if BTC reaches $40,000 by December 31, 2030.",
    )
    assert result.world_state is not None
    names = {v.name for v in result.world_state.state_variables}
    assert "spot_price" in names


# ---------------------------------------------------------------------------
# Part 2 — Event Intelligence: syndicated-article dedup reaching
# confirmation_count
# ---------------------------------------------------------------------------


def test_syndicated_articles_about_same_event_deduplicate_confirmation_count(storage: Storage) -> None:
    """N articles that are the SAME underlying event (verbatim syndicated
    title, published by N different outlets — a realistic wire-story
    scenario) must NOT inflate confirmation_count to N. claims.py's
    group_claims_by_normalization already computes the real per-event
    dedup (same actors/action/status -> identical normalized claim); this
    test proves it actually reaches IndependentEvidenceResult.confirmation_
    count, not just persistence side effects."""
    question = "Will Governor Smith resign before the end of his term?"
    resolution = "Resolves YES if Governor Smith resigns. Resolves NO otherwise."
    same_title = "Governor Smith resigns amid corruption investigation"
    _link_evidence(
        storage, "polymarket", "m1", question,
        [
            (same_title, "reuters", "https://reuters.com/a", 0.9),
            (same_title, "apnews", "https://apnews.com/a", 0.9),
            (same_title, "bbc", "https://bbc.com/a", 0.9),
            (same_title, "cnn", "https://cnn.com/a", 0.9),
            (same_title, "nytimes", "https://nytimes.com/a", 0.9),
        ],
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m1", question, resolution, market_yes_price=0.5,
    )
    assert ev.available
    # 5 distinct domains, but ONE real underlying event -> confirmation_
    # count must be deduplicated down from the raw domain count.
    assert len({f.source_domain for f in ev.evidence_for_yes}) == 5
    assert ev.confirmation_count == 1


def test_genuinely_distinct_events_are_not_over_deduplicated(storage: Storage) -> None:
    """Control case: 3 articles about the SAME actor/action but genuinely
    distinct resignations described with distinct actor names must NOT be
    collapsed into 1 — the dedup is per-real-event, not per-market."""
    question = "Will Governor Smith resign before the end of his term?"
    resolution = "Resolves YES if Governor Smith resigns. Resolves NO otherwise."
    _link_evidence(
        storage, "polymarket", "m2", question,
        [
            ("Governor Smith resigns amid corruption investigation", "reuters", "https://reuters.com/a", 0.9),
            ("Senator Jones resigns amid corruption investigation", "apnews", "https://apnews.com/b", 0.9),
            ("Mayor Lee resigns amid corruption investigation", "bbc", "https://bbc.com/c", 0.9),
        ],
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m2", question, resolution, market_yes_price=0.5,
    )
    assert ev.available
    assert len({f.source_domain for f in ev.evidence_for_yes}) == 3
    # Distinct actors -> distinct normalized claims -> NOT collapsed to 1.
    assert ev.confirmation_count == 3


def test_dedup_never_exceeds_real_distinct_domain_count(storage: Storage) -> None:
    """Sanity/regression guard: the dedup fix must only ever REDUCE
    confirmation_count relative to the domain-based count, never inflate
    it past the number of distinct sources actually observed."""
    question = "Will Governor Smith resign before the end of his term?"
    resolution = "Resolves YES if Governor Smith resigns. Resolves NO otherwise."
    same_title = "Governor Smith resigns amid corruption investigation"
    _link_evidence(
        storage, "polymarket", "m3", question,
        [(same_title, "reuters", "https://reuters.com/a", 0.9),
         (same_title, "apnews", "https://apnews.com/a", 0.9)],
    )
    ev = compute_independent_evidence(
        storage.connection, "polymarket", "m3", question, resolution, market_yes_price=0.5,
    )
    assert ev.available
    domain_count = len({f.source_domain for f in ev.evidence_for_yes})
    assert ev.confirmation_count <= domain_count
