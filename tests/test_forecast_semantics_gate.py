"""Permanent regression test for the "Bab el-Mandeb problem" (Block A).

Named failure pattern (project owner's brief, confirmed live against
data/polymarketpulse.db on 2026-08-11 for the real market
"polymarket:2911874" / "Bab el-Mandeb Strait effectively closed by August
31?"): the market price sits at ~4.25% YES, while the raw specialized-model
hypothesis (`model_hypothesis_probability`) comes out around 70% -- a large,
confident-looking number -- but with `comparable_sample_size == 0` (no real
historical comparables) and no genuine independent evidence. Live tracing
confirms the CURRENT code already suppresses this correctly end-to-end:
`forecast_maturity="NO_FORECAST"`, `forecast_status="FORECAST_SUPPRESSED"`,
`evidence_backed_probability=None`, `published_forecast_probability=None`.
This test locks that behavior in as a permanent, general rule (not
hardcoded to one market_id, per this project's "fix the general rule, not
the specific case" discipline) so a future change to the specialized-model
defaults, the comparable-count logic, or the Block A gating in
`prediction/engine.py` cannot silently regress back to publishing a
confident number resting on inadequate evidence.

The synthetic fixture below reconstructs the general shape of the problem:
a market whose price is near zero, for which a specialized/base-rate model
would naturally produce a high raw hypothesis, but for which there are
zero linked news events and zero historical comparables. It must never
yield a published forecast, regardless of how confident the raw model
hypothesis looks.
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


def _strait_market(pmid: str, question: str) -> Market:
    return Market(
        provider="polymarket", provider_market_id=pmid, condition_id="",
        question=question, slug=pmid,
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
        matched_terms=("strait",), confidence=confidence,
    )
    storage.save_news_market_link(row_id, link)


def test_low_price_high_hypothesis_with_no_evidence_never_publishes(
    storage: Storage,
) -> None:
    """General reconstruction of the Bab el-Mandeb pattern: a near-zero
    market price, zero linked evidence, zero historical comparables. No
    matter what the raw specialized-model hypothesis looks like, this must
    never mature past NO_FORECAST and must never populate
    evidence_backed_probability or published_forecast_probability.
    """
    market = _strait_market(
        "test-strait-closure-pattern",
        "Some strait effectively closed by end of month?",
    )
    # Deliberately: no _link_news calls, no historical comparable seeding --
    # this is the "confident number resting on nothing" shape.

    result = compute_prediction(
        storage.connection,
        "test-strait-closure-pattern",
        "polymarket",
        "test-strait-closure-pattern",
        "geopolitics",
        0.0425,  # market price: ~4.25% YES, matching the real live case
        1000,
        90,
        0,
        None,
        True,
        question=market.question,
    )

    assert result.market_probability == pytest.approx(0.0425)
    assert result.comparable_sample_size == 0

    # The core regression assertion: regardless of what model_hypothesis_
    # probability comes out to (it may legitimately be a confident number
    # from a naive/base-rate specialized model -- the real live market this
    # guards produces ~0.70 here), it must never be treated as evidence-
    # backed or publishable when there is no real evidence and no real
    # historical comparables behind it.
    assert result.evidence_backed_probability is None
    assert result.published_forecast_probability is None
    assert result.forecast_maturity == "NO_FORECAST"
    assert result.forecast_status in ("NO_FORECAST", "FORECAST_SUPPRESSED")


def test_partial_evidence_is_evidence_backed_but_not_yet_publishable(
    storage: Storage,
) -> None:
    """Complementary case distinguishing the two Block A gate tiers: a
    forecast can be "evidence_backed" (real DIRECT-tier evidence exists,
    maturity has cleared HYPOTHESIS) while still not being "published"
    because it has not reached SUPPORTED_FORECAST (e.g. a CRITICAL data gap
    such as zero historical comparables remains). This guards against
    collapsing the two distinct Block A concepts back into one.
    """
    market = _strait_market(
        "test-partial-evidence-pattern",
        "Trump out as President by August 31?",
    )
    _link_news(
        storage, market, "Trump resignation confirmed by White House officials",
        "reuters", "https://reuters.com/a", confidence=0.6, hours_ago=1,
    )
    _link_news(
        storage, market, "White House confirms Trump resignation agreement signed",
        "apnews", "https://apnews.com/b", confidence=0.6, hours_ago=2,
    )

    result = compute_prediction(
        storage.connection, "test-partial-evidence-pattern", "polymarket",
        "test-partial-evidence-pattern", "geopolitics",
        0.05, 50000, 90, 0, None, True, question=market.question,
    )

    assert result.forecast_status != "FORECAST_SUPPRESSED"
    assert result.independent_probability is not None
    # Real evidence exists -> evidence-backed, even though...
    assert result.evidence_backed_probability is not None
    # ...zero historical comparables is a CRITICAL data gap, so the
    # forecast has not reached SUPPORTED_FORECAST and must not publish.
    assert result.forecast_maturity == "PARTIAL_FORECAST"
    assert result.published_forecast_probability is None
