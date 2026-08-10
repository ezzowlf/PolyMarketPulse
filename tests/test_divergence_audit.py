"""Phase M: divergence red-team audit (see prediction/divergence_audit.py).

Covers the 4 scenarios from the task spec:
- large divergence + strong evidence (DATA_FITTED history, DIRECT-tier
  evidence) -> PASS, not suppressed.
- large divergence + weak/heuristic-only evidence -> REJECT, suppressed
  (reuses the same fixtures as the Phase B4 divergence-suppression tests
  to confirm the new audit preserves that behavior).
- large divergence with some real issues but not disqualifying ones ->
  WARN, forecast stands but is flagged.
- small divergence (under threshold) never triggers the audit at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _trump_market(pmid: str) -> Market:
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


def _seed_history(storage: Storage, n_yes: int, n_no: int, category: str, question_prefix: str) -> None:
    now = datetime.now(UTC).isoformat()
    provider = "polymarket"
    for i in range(n_yes):
        pmid = f"{question_prefix}-yes-{i}"
        storage.connection.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, condition_id, question, slug, "
            "category, classified_category, url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, '', ?, ?, ?, ?, 'https://x', ?, ?)",
            (pmid, provider, pmid, f"{question_prefix} {i}?", pmid, category, category, now, now),
        )
        storage.connection.execute(
            "INSERT INTO market_resolutions (provider, provider_market_id, status, winning_outcome, resolved_at, detected_at) "
            "VALUES (?, ?, 'resolved', 'Yes', ?, ?)",
            (provider, pmid, now, now),
        )
    for i in range(n_no):
        pmid = f"{question_prefix}-no-{i}"
        storage.connection.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, condition_id, question, slug, "
            "category, classified_category, url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, '', ?, ?, ?, ?, 'https://x', ?, ?)",
            (pmid, provider, pmid, f"{question_prefix} {i}?", pmid, category, category, now, now),
        )
        storage.connection.execute(
            "INSERT INTO market_resolutions (provider, provider_market_id, status, winning_outcome, resolved_at, detected_at) "
            "VALUES (?, ?, 'resolved', 'No', ?, ?)",
            (provider, pmid, now, now),
        )
    storage.connection.commit()


def test_small_divergence_never_triggers_audit(storage: Storage) -> None:
    market = _trump_market("small-divergence")
    result = compute_prediction(
        storage.connection, "small-divergence", "polymarket", "small-divergence", "geopolitics",
        0.5, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.divergence_audit is not None
    assert result.divergence_audit.triggered is False
    assert result.divergence_audit.verdict is None


def test_large_divergence_strong_evidence_yields_pass(storage: Storage) -> None:
    market = _trump_market("strong-pass-divergence")
    _link_news(
        storage, market, "Trump resignation confirmed by White House officials",
        "reuters", "https://reuters.com/a", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "White House confirms Trump resignation agreement signed",
        "apnews", "https://apnews.com/b", confidence=0.6, hours_ago=2,
    )
    result = compute_prediction(
        storage.connection, "strong-pass-divergence", "polymarket", "strong-pass-divergence", "geopolitics",
        0.05, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.divergence_audit is not None
    assert result.divergence_audit.triggered is True
    assert result.divergence_audit.verdict in ("PASS", "WARN")
    assert result.forecast_status != "FORECAST_SUPPRESSED"
    assert result.independent_probability is not None
    # The hard-gate check specifically must PASS given 2 DIRECT-tier
    # independently-confirming sources.
    by_name = {c.name: c for c in result.divergence_audit.checks}
    assert by_name["evidentiary_sufficiency"].verdict == "PASS"


def test_large_divergence_weak_evidence_yields_reject_and_suppression(storage: Storage) -> None:
    market = _trump_market("weak-reject-divergence")
    _link_news(
        storage, market, "Trump faces new calls to step down amid pressure", "outlet-a",
        "https://outlet-a.example/1", confidence=0.5, hours_ago=1,
    )
    _link_news(
        storage, market, "Activists urge Trump to resign immediately", "outlet-b",
        "https://outlet-b.example/2", confidence=0.6, hours_ago=2,
    )
    result = compute_prediction(
        storage.connection, "weak-reject-divergence", "polymarket", "weak-reject-divergence", "geopolitics",
        0.85, 50000, 90, 0, None, True, question=market.question,
    )
    assert result.divergence_audit is not None
    assert result.divergence_audit.triggered is True
    assert result.divergence_audit.verdict == "REJECT"
    by_name = {c.name: c for c in result.divergence_audit.checks}
    assert by_name["evidentiary_sufficiency"].verdict == "REJECT"
    assert by_name["evidentiary_sufficiency"].hard_fail is True
    # Suppression behavior preserved from Phase B4.
    assert result.forecast_status == "FORECAST_SUPPRESSED"
    assert result.independent_probability is not None
    assert result.forecast_suppression_reason is not None


def test_large_divergence_with_real_but_non_disqualifying_issues_yields_warn(storage: Storage) -> None:
    # Strong enough evidence to pass the hard evidentiary_sufficiency gate
    # (2 DIRECT-tier, independently confirming sources) but with a real,
    # non-disqualifying issue flagged: no resolution_text supplied (so
    # resolution_rule_presence / proposition_clarity WARN rather than pass).
    market = _trump_market("warn-divergence")
    _link_news(
        storage, market, "Trump resignation confirmed by White House officials",
        "reuters", "https://reuters.com/a", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "White House confirms Trump resignation agreement signed",
        "apnews", "https://apnews.com/b", confidence=0.6, hours_ago=2,
    )
    result = compute_prediction(
        storage.connection, "warn-divergence", "polymarket", "warn-divergence", "geopolitics",
        0.05, 50000, 90, 0, None, True, question=market.question,
        # no resolution_text -> resolution_rule_presence check WARNs.
    )
    assert result.divergence_audit is not None
    assert result.divergence_audit.triggered is True
    by_name = {c.name: c for c in result.divergence_audit.checks}
    assert by_name["evidentiary_sufficiency"].verdict == "PASS"
    assert by_name["resolution_rule_presence"].verdict == "WARN"
    # At least one real WARN and no REJECT -> overall verdict WARN.
    assert result.divergence_audit.verdict == "WARN"
    assert result.forecast_status != "FORECAST_SUPPRESSED"
    assert result.independent_probability is not None
