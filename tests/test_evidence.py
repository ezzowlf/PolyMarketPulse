from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction.evidence import compute_independent_evidence
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _link_news(storage: Storage, market: Market, title: str, source: str, source_url: str, hours_ago: float, confidence: float = 0.5) -> None:
    event = NewsEvent(
        source=source, source_url=source_url, title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(
        news_event=event, market=market, match_reason="shared_terms",
        matched_terms=("test",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


def _market() -> Market:
    return Market(
        provider="polymarket", provider_market_id="evidence-1", condition_id="",
        question="Will the ceasefire agreement be confirmed?", slug="evidence-1",
    )


def test_no_linked_news_is_unavailable(storage: Storage) -> None:
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question="Will the ceasefire agreement be confirmed?", resolution_text=None,
        market_yes_price=0.9,
    )
    assert result.available is False
    assert "keine unabhängige Schätzung möglich" in result.detail


def test_single_linked_news_item_still_unavailable(storage: Storage) -> None:
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by officials", "reuters", "https://reuters.com/a", hours_ago=2)
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.9,
    )
    assert result.available is False


def test_single_linked_news_item_still_extracts_and_persists_a_real_claim(storage: Storage) -> None:
    """Regression for the real root cause behind Hormuz-shaped markets never
    accumulating claims: exactly 1 linked article used to short-circuit
    compute_independent_evidence BEFORE the per-article extraction/
    persistence loop ever ran, even though claim persistence is documented
    as unconditional on the probability outcome. A single real article must
    still produce a real, persisted claim — the probability itself
    correctly stays unavailable (1 article is not independent confirmation)."""
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by officials", "reuters", "https://reuters.com/a", hours_ago=2)
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.9,
    )
    assert result.available is False  # unchanged: still not enough for an estimate

    claim_rows = storage.connection.execute("SELECT COUNT(*) FROM claims").fetchone()
    assert claim_rows[0] >= 1  # but a real claim was extracted and persisted


def test_single_article_stays_visible_on_the_unavailable_result(storage: Storage) -> None:
    """Real requirement: 'eine relevante Quelle vorhanden, unabhängige
    Bestätigung fehlt' must be a real, inspectable statement — the single
    real article must still appear in evidence_for_yes/no on the returned
    (unavailable) result, not be silently discarded just because no
    probability could be computed from it."""
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by officials", "reuters", "https://reuters.com/a", hours_ago=2)
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.9,
    )
    assert result.available is False
    total_visible = len(result.evidence_for_yes) + len(result.evidence_for_no) + len(result.discarded_evidence)
    assert total_visible == 1
    assert "relevante Quelle" in result.detail


def test_confirming_evidence_from_independent_sources_produces_estimate_not_anchored_to_market_price(
    storage: Storage,
) -> None:
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", "https://reuters.com/a", hours_ago=1)
    _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", "https://apnews.com/b", hours_ago=3)

    # Market price is deliberately extreme (0.05) — if the independent
    # estimate merely echoed the market, it would also sit near 0.05.
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.05,
    )
    assert result.available is True
    assert result.independent_yes_probability is not None
    # Positive-sentiment confirming evidence should push the independent
    # estimate above the neutral 0.5 prior, nowhere near the 0.05 market price.
    assert result.independent_yes_probability > 0.5
    assert result.confirmation_count >= 1
    assert result.divergence is not None
    assert result.divergence > 0
    assert result.information_edge_score is not None


def test_contradictory_evidence_is_flagged(storage: Storage) -> None:
    market = _market()
    _link_news(storage, market, "Ceasefire confirmed, deal signed", "reuters", "https://reuters.com/a", hours_ago=1)
    _link_news(storage, market, "Ceasefire denied, talks collapse", "bbc", "https://bbc.com/b", hours_ago=1)

    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question=market.question, resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is True
    assert result.contradiction_detected is True


def _seed_structured_claim(
    storage: Storage, claim_id: str, provider_market_id: str, direction: str, claim_type: str,
) -> None:
    from polymarketpulse.claims import Claim

    claim = Claim(
        claim_id=claim_id, subject="test subject", predicate="test predicate", object=None,
        speaker=None, source_id="imf_portwatch", source_url="https://portwatch.imf.org",
        timestamp=datetime.now(UTC), verification_status="PRIMARY_CONFIRMED", confidence=0.95,
        direction=direction,
    )
    storage.save_claim(claim)
    storage.save_claim_market_link(claim_id, "polymarket", provider_market_id, claim_type)


def test_direct_resolution_structured_claim_alone_satisfies_evidence_gate(storage: Storage) -> None:
    """Real claims-to-forecast integration: a single DIRECT_RESOLUTION
    structured claim (e.g. IMF PortWatch data directly compared against
    the market's own resolution threshold) is categorically stronger than
    one ambiguous news article, so it alone must be able to clear the
    evidence-sufficiency gate that a single article cannot."""
    _seed_structured_claim(storage, "struct-1", "evidence-1", "negative", "DIRECT_RESOLUTION")
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question="Will the strait reopen?", resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is True
    assert len(result.evidence_for_no) == 1
    assert result.evidence_for_no[0].relation_label == "DIRECT_NO"


def test_path_step_structured_claim_never_counts_as_evidence(storage: Storage) -> None:
    """The explicit double-counting guard: a PATH_STEP claim (confirms one
    resolution-path step, e.g. GovTrack "House passed") must never also be
    folded into the yes/no evidence math -- that would double-count the
    same real fact through two channels."""
    _seed_structured_claim(storage, "struct-2", "evidence-1", "positive", "PATH_STEP")
    result = compute_independent_evidence(
        storage.connection, provider="polymarket", provider_market_id="evidence-1",
        question="Will the bill be signed?", resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is False  # PATH_STEP alone must not satisfy the gate
    assert len(result.evidence_for_yes) == 0
    assert len(result.evidence_for_no) == 0


def test_unavailable_when_no_evidence_infrastructure(tmp_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    result = compute_independent_evidence(
        conn, provider="polymarket", provider_market_id="x", question="Q?",
        resolution_text=None, market_yes_price=0.5,
    )
    assert result.available is False
    conn.close()
