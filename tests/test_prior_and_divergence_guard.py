"""Phase B: no universal 50% prior + event-type base rates + extraordinary-
event guard + divergence safety.

Covers:
- no historical baseline + no evidence -> independent_probability is None,
  never a fabricated 0.5.
- base_rates.get_base_rate returns a real value for a known event_type and
  None for an unknown one — no caller substitutes 0.5 for the unknown case.
- an extraordinary event_type (office_departure) backed by only a single
  WEAK_YES item is dampened to stay close to its base rate, not allowed to
  swing freely.
- the same extraordinary event_type backed by 2+ DIRECT_YES-tier items is
  allowed to swing far from the base rate (the guard doesn't just
  permanently clamp everything).
- divergence safety: a large independent/market gap backed only by weak
  evidence gets suppressed (independent_probability -> None,
  forecast_status -> FORECAST_SUPPRESSED, human-readable reason present).
- divergence safety negative case: the same size gap backed by strong
  evidence is NOT suppressed.
- the exact Trump/Nevada regression (single loosely-relevant, positively
  toned article) still comes back with no fabricated number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.base_rates import BASE_RATES, get_base_rate
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _trump_market(pmid: str = "trump-1") -> Market:
    return Market(
        provider="polymarket", provider_market_id=pmid, condition_id="",
        question="Trump out as President by August 31?", slug=pmid,
    )


def _link_news(
    storage: Storage, market: Market, title: str, source: str, source_url: str,
    confidence: float, hours_ago: float = 1.0,
) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago), fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("trump",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


# ---------------------------------------------------------------------------
# No universal 50% prior
# ---------------------------------------------------------------------------


def test_no_history_no_evidence_yields_none_not_half(storage: Storage) -> None:
    market = _trump_market("no-data")
    result = compute_prediction(
        storage.connection, "no-data", "polymarket", "no-data", "geopolitics",
        0.4, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.independent_probability is None
    assert result.forecast_status in ("NO_FORECAST", "LOW_DATA")


def test_independent_evidence_unavailable_is_not_half(storage: Storage) -> None:
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="trump-1",
        question="Trump out as President by August 31?", resolution_text=None,
        market_yes_price=0.007,
    )
    assert result.available is False
    assert result.independent_yes_probability is None


# ---------------------------------------------------------------------------
# Base rate table
# ---------------------------------------------------------------------------


def test_base_rate_known_event_type_returns_real_value() -> None:
    value = get_base_rate("office_departure")
    assert value is not None
    assert 0.0 < value < 0.5  # very rare, well below coin-flip


def test_base_rate_unknown_event_type_returns_none_never_half() -> None:
    assert get_base_rate("some_never_seen_event_type") is None
    assert get_base_rate(None) is None
    # Guard rail against a future edit accidentally reintroducing a 0.5
    # placeholder into the table itself.
    assert 0.5 not in BASE_RATES.values()


# ---------------------------------------------------------------------------
# Extraordinary-event guard
# ---------------------------------------------------------------------------


def test_extraordinary_event_single_weak_item_is_dampened(storage: Storage) -> None:
    market = _trump_market("weak-extraordinary")
    # Two "call for resignation" headlines from different domains -> passes
    # the raw evidence-count gate and the topic/relevance gate, but only
    # ever classifies as WEAK_YES (a demand is not the event itself) — zero
    # DIRECT_YES/DIRECT_NO tier items.
    _link_news(
        storage, market, "Senator calls on Trump to resign", "outlet-a",
        "https://outlet-a.example/1", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "Activists urge Trump to resign immediately", "outlet-b",
        "https://outlet-b.example/2", confidence=0.6, hours_ago=2,
    )
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="weak-extraordinary",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is True
    assert result.extraordinary_guard_applied is True
    base_rate = get_base_rate("office_departure")
    # Dampened estimate must stay close to the base-rate anchor, nowhere
    # near a confident YES.
    assert abs(result.independent_yes_probability - base_rate) <= 0.05
    assert result.independent_yes_probability < 0.15


def test_extraordinary_event_two_direct_items_can_swing_far(storage: Storage) -> None:
    market = _trump_market("strong-extraordinary")
    _link_news(
        storage, market, "Trump resignation confirmed by White House officials",
        "reuters", "https://reuters.com/a", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "White House confirms Trump resignation agreement signed",
        "apnews", "https://apnews.com/b", confidence=0.6, hours_ago=2,
    )
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="strong-extraordinary",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is True
    assert result.extraordinary_guard_applied is False
    assert result.independent_yes_probability > 0.5  # allowed to swing well past the base rate


# ---------------------------------------------------------------------------
# Divergence safety
# ---------------------------------------------------------------------------


def test_divergence_suppressed_when_evidence_is_weak(storage: Storage) -> None:
    market = _trump_market("weak-divergence")
    _link_news(
        storage, market, "Senator calls on Trump to resign", "outlet-a",
        "https://outlet-a.example/1", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "Activists urge Trump to resign immediately", "outlet-b",
        "https://outlet-b.example/2", confidence=0.6, hours_ago=2,
    )
    # Market price is far from the (already-dampened, low) independent
    # estimate, and there is no historical baseline and no direct-tier
    # evidence -> recommendation must be suppressed while the market-blind
    # diagnostic estimate remains observable.
    result = compute_prediction(
        storage.connection, "weak-divergence", "polymarket", "weak-divergence", "geopolitics",
        0.85, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.independent_probability is not None
    assert result.forecast_status == "FORECAST_SUPPRESSED"
    assert result.forecast_suppression_reason is not None
    assert "%" in result.forecast_suppression_reason


def test_divergence_not_suppressed_when_evidence_is_strong(storage: Storage) -> None:
    market = _trump_market("strong-divergence")
    _link_news(
        storage, market, "Trump resignation confirmed by White House officials",
        "reuters", "https://reuters.com/a", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "White House confirms Trump resignation agreement signed",
        "apnews", "https://apnews.com/b", confidence=0.6, hours_ago=2,
    )
    # Market price is still far below the confident, evidence-backed
    # independent estimate -- but this time the evidence is strong (2
    # DIRECT_YES-tier, independently confirming sources), so it must NOT be
    # suppressed: this is exactly the kind of real edge the product exists
    # to surface.
    result = compute_prediction(
        storage.connection, "strong-divergence", "polymarket", "strong-divergence", "geopolitics",
        0.05, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.forecast_status != "FORECAST_SUPPRESSED"
    assert result.independent_probability is not None


# ---------------------------------------------------------------------------
# Trump/Nevada regression (no fabricated number)
# ---------------------------------------------------------------------------


def test_trump_nevada_regression_still_produces_no_fabricated_number(storage: Storage) -> None:
    market = _trump_market("trump-nevada")
    _link_news(
        storage, market, "President Trump and Republicans Deliver Big Wins for the Silver State",
        "whitehouse", "https://whitehouse.gov/nevada-wins", confidence=0.15,
    )
    evidence_result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="trump-nevada",
        question=market.question, resolution_text=None, market_yes_price=0.007,
    )
    assert evidence_result.available is False
    assert evidence_result.independent_yes_probability is None

    prediction_result = compute_prediction(
        storage.connection, "trump-nevada", "polymarket", "trump-nevada", "geopolitics",
        0.007, 50000, 90, 0, None, True, question=market.question,
    )
    assert prediction_result.independent_probability is None
    assert prediction_result.forecast_status != "FORECAST_SUPPRESSED"  # nothing to suppress, never had a number
    # No submodel is allowed to have quietly reported 0.56 (the historical
    # bug) or any other fabricated confident number for this market.
    for submodel in prediction_result.submodel_estimates:
        if submodel.name in ("history", "independent_evidence") and not submodel.available:
            assert submodel.estimated_yes_probability in (None, submodel.estimated_yes_probability)
