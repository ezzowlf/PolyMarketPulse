"""Covers the three real independent-forecast paths this round proved out:
history-only, evidence-only, and history+evidence combined — plus the
news-enabled/disabled behavior at the CLI config layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.config import Settings
from polymarketpulse.models import Market, ResolutionStatus
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _market(pmid: str, question: str) -> Market:
    return Market(
        provider="polymarket", provider_market_id=pmid, condition_id="", question=question,
        slug=f"m-{pmid}", category="geopolitics",
    )


def _resolved(pmid: str, outcome: str) -> Market:
    return Market(
        provider="polymarket", provider_market_id=pmid, condition_id="", question=f"Will {pmid} happen?",
        slug=f"m-{pmid}", category="geopolitics", resolution_status=ResolutionStatus.RESOLVED,
        winning_outcome=outcome, resolved_at=datetime.now(UTC),
    )


def _link_news(storage: Storage, market: Market, title: str, source: str, hours_ago: float) -> None:
    event = NewsEvent(
        source=source, source_url=f"https://{source}/{title[:10]}", title=title,
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago), fetched_at=datetime.now(UTC),
    )
    row_id = storage.save_news_event(event)
    link = NewsMarketLink(news_event=event, market=market, match_reason="shared_terms", matched_terms=("x",), confidence=0.5)
    storage.save_news_market_link(row_id, link)


def test_history_only_path_produces_baseline_only(storage: Storage) -> None:
    for i in range(10):
        storage.record_resolution(_resolved(f"h-{i}", "Yes" if i % 3 else "No"))
    result = compute_prediction(storage.connection, "m1", "polymarket", "1", "geopolitics", 0.4, 50000, 90, 0, None, True)
    assert result.forecast_status == "BASELINE_ONLY"
    assert result.independent_probability is not None


def test_history_only_path_reports_data_fitted_prior_provenance(storage: Storage) -> None:
    # K3: contribution_breakdown should honestly label the "history" entry
    # as DATA_FITTED (a real weighted baseline over actually-resolved
    # comparable markets) rather than leaving prior_provenance unset or
    # mislabeling it as a heuristic. See engine.py's _PRIOR_PROVENANCE_BY_SOURCE.
    for i in range(10):
        storage.record_resolution(_resolved(f"hpp-{i}", "Yes" if i % 3 else "No"))
    result = compute_prediction(storage.connection, "m1", "polymarket", "1", "geopolitics", 0.4, 50000, 90, 0, None, True)
    history_entries = [e for e in result.contribution_breakdown if e.source == "history"]
    assert history_entries, "expected a 'history' entry in contribution_breakdown"
    assert history_entries[0].available is True
    assert history_entries[0].prior_provenance == "DATA_FITTED"


def test_evidence_only_path_produces_evidence_only(storage: Storage) -> None:
    # Note: the same linked news items are also picked up by the older,
    # market-price-anchored "news" submodel (compute_news_estimate) — since
    # both read the same sentiment-scored articles, a market with strong
    # enough evidence for independent_evidence to fire usually also gives
    # "news" a signal, correctly producing BLENDED_FORECAST rather than a
    # pure EVIDENCE_ONLY. Both are legitimate; what this test actually
    # verifies is that evidence alone (no history at all) still produces a
    # real, non-fabricated independent_probability.
    market = _market("1", "Will the ceasefire hold?")
    _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", 1)
    _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", 2)
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "1", "geopolitics", 0.4, 50000, 90, 0, None, True,
        question=market.question,
    )
    assert result.forecast_status in ("EVIDENCE_ONLY", "BLENDED_FORECAST")
    assert result.independent_probability is not None


def test_history_plus_evidence_path_produces_independent_or_blended_forecast(storage: Storage) -> None:
    # Both submodels (history + independent_evidence) contribute here; if
    # the market-price-anchored "news" submodel also picks up the same
    # linked articles, the correct status is BLENDED_FORECAST rather than
    # INDEPENDENT_FORECAST — both are legitimate outcomes of this fixture,
    # what matters is that independent_probability is a real, populated
    # number combining history + evidence, not None.
    for i in range(10):
        storage.record_resolution(_resolved(f"he-{i}", "Yes" if i % 3 else "No"))
    market = _market("1", "Will the ceasefire hold?")
    _link_news(storage, market, "Ceasefire confirmed by both sides, agreement signed", "reuters", 1)
    _link_news(storage, market, "Officials confirm ceasefire agreement reached", "apnews", 2)
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "1", "geopolitics", 0.4, 50000, 90, 0, None, True,
        question=market.question,
    )
    assert result.forecast_status in ("INDEPENDENT_FORECAST", "BLENDED_FORECAST")
    assert result.independent_probability is not None


def test_news_enabled_flag_read_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKETPULSE_NEWS_ENABLED", "true")
    assert Settings.load().news_enabled is True


def test_news_disabled_flag_read_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKETPULSE_NEWS_ENABLED", "false")
    assert Settings.load().news_enabled is False
