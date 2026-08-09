"""Phase F — real, per-market, quality-weighted ensemble.

Covers:
  F1. Specialized (Phase E) models are wired into combine_submodels() via
      specialized_router.route_to_specialized_model(), and never receive
      the market price as an input (hard invariant, tested structurally
      for every specialized model, not just behaviorally for one).
  F2. Weighting is a genuine function of each submodel's own quality
      signal (history's effective sample size / tier, evidence's
      source_quality_score + confirmation_count, specialized models' own
      `confidence` output) — never a flat "available -> fixed weight".
  F3. independent_probability stays strictly market-blind end-to-end,
      across markets that route to different specialized models.
  F4. contribution_breakdown reflects the real per-market model set:
      eligible-and-used, eligible-but-unavailable, and not-eligible-for-
      this-market-type are all distinguishable.
  F5. A market that isn't eligible for any specialized model still falls
      back cleanly to history/evidence-only combination (no regression).
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import NewsMarketLink
from polymarketpulse.prediction import compute_prediction
from polymarketpulse.prediction.ensemble import quality_scaled_weight
from polymarketpulse.prediction.geopolitics import analyze_geopolitics
from polymarketpulse.prediction.macro import analyze_macro
from polymarketpulse.prediction.politics import analyze_politics
from polymarketpulse.prediction.quant import analyze_quant
from polymarketpulse.prediction.sports import analyze_sports
from polymarketpulse.providers.coingecko import PriceData
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _seed_history(
    storage: Storage, n_yes: int, n_no: int, category: str, provider: str = "polymarket",
    question_prefix: str = "Historical comparable market", event_type: str | None = None,
) -> None:
    """Directly populates the real `markets` / `market_resolutions` tables
    (post-migration schema) with resolved comparable cases sharing
    `classified_category` with the target market, so history.py's Phase D
    similarity-weighted baseline (category_match weight) finds them.

    NOTE: `category` here must be the Phase C fixed-taxonomy label
    classify_market() would actually assign to the *target* question (e.g.
    "CRYPTO", "GEOPOLITICS", "OTHER") — not an arbitrary free-text label —
    since that's what history.py's candidate scorer compares against (see
    classification.py's category constants).

    `event_type` (Part 1 correctness fix, 2026-08): history.py's
    compatibility gate now requires category match AND (event_type match OR
    entity overlap) before a candidate is even scored — a category-only
    match ("both CRYPTO") is no longer enough by itself. Callers that want
    their seeded comparables to actually be usable (not silently
    gate-rejected to zero weight) must pass the same event_type the target
    question will parse to (e.g. "price_above" for a BTC/threshold
    question)."""
    now = datetime.now(UTC).isoformat()
    for i in range(n_yes):
        pmid = f"{question_prefix}-yes-{i}"
        storage.connection.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, condition_id, question, slug, "
            "category, classified_category, event_type, url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 'https://x', ?, ?)",
            (pmid, provider, pmid, f"{question_prefix} {i}?", pmid, category, category, event_type, now, now),
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
            "category, classified_category, event_type, url, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, 'https://x', ?, ?)",
            (pmid, provider, pmid, f"{question_prefix} {i}?", pmid, category, category, event_type, now, now),
        )
        storage.connection.execute(
            "INSERT INTO market_resolutions (provider, provider_market_id, status, winning_outcome, resolved_at, detected_at) "
            "VALUES (?, ?, 'resolved', 'No', ?, ?)",
            (provider, pmid, now, now),
        )
    storage.connection.commit()


class _FakeMarket:
    """Minimal stand-in for models.Market — NewsMarketLink only stores it,
    save_news_market_link() never reads its fields, so a tiny shim avoids
    depending on the full Market dataclass's many required fields."""

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
        # save_news_market_link resolves provider/provider_market_id from
        # its own explicit args below, not from `link.market` (see
        # evidence.py callers) — pass them explicitly via the storage API.
        storage.connection.execute(
            "INSERT INTO news_market_links (news_event_id, provider, provider_market_id, "
            "match_reason, matched_terms, confidence, confirmed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'automatic', ?)",
            (row_id, provider, provider_market_id, link.match_reason, "", confidence, now.isoformat()),
        )
    storage.connection.commit()


# ---------------------------------------------------------------------------
# F1 — hard invariant: no specialized model ever takes a market-price input
# ---------------------------------------------------------------------------

def test_quant_never_takes_market_price_param() -> None:
    sig = inspect.signature(analyze_quant)
    assert "market_yes_price" not in sig.parameters
    assert "market_probability" not in sig.parameters


def test_politics_never_takes_market_price_param() -> None:
    sig = inspect.signature(analyze_politics)
    assert "market_yes_price" not in sig.parameters
    assert "market_probability" not in sig.parameters


def test_geopolitics_never_takes_market_price_param() -> None:
    sig = inspect.signature(analyze_geopolitics)
    assert "market_yes_price" not in sig.parameters
    assert "market_probability" not in sig.parameters


def test_macro_never_takes_market_price_param() -> None:
    sig = inspect.signature(analyze_macro)
    assert "market_yes_price" not in sig.parameters
    assert "market_probability" not in sig.parameters


def test_sports_never_takes_market_price_param() -> None:
    sig = inspect.signature(analyze_sports)
    assert "market_yes_price" not in sig.parameters
    assert "market_probability" not in sig.parameters


# ---------------------------------------------------------------------------
# quality_scaled_weight — the shared weighting primitive
# ---------------------------------------------------------------------------

def test_quality_scaled_weight_scales_linearly_with_quality() -> None:
    assert quality_scaled_weight(0.45, 1.0) == 0.45
    assert quality_scaled_weight(0.45, 0.5) == 0.225
    assert quality_scaled_weight(0.45, 0.0) == 0.0


def test_quality_scaled_weight_clamps_out_of_range_quality() -> None:
    assert quality_scaled_weight(0.45, 5.0) == 0.45  # never exceeds base_weight
    assert quality_scaled_weight(0.45, -3.0) == 0.0  # never negative


# ---------------------------------------------------------------------------
# F1/F2 — quant-routed market integration
# ---------------------------------------------------------------------------

def test_quant_routed_market_incorporates_quant_estimate(storage: Storage, monkeypatch) -> None:
    """A BTC price-threshold market should route to quant.py and quant's
    estimate should show up, with nonzero weight, in independent_probability
    (and in contribution_breakdown as available/eligible)."""
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.resolve_coingecko_id", lambda asset: "bitcoin"
    )
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.fetch_price_and_volatility",
        lambda coingecko_id: PriceData(current_price=50000.0, daily_volatility=0.03, days_of_history=90),
    )
    _seed_history(
        storage, n_yes=15, n_no=5, category="CRYPTO", question_prefix="crypto-hist",
        event_type="price_above",
    )
    question = "Will Bitcoin reach $40,000 by December 31, 2030?"
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "crypto", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text="Resolves YES if BTC reaches $40,000 by December 31, 2030.",
    )
    by_source = {c.source: c for c in result.contribution_breakdown}
    assert "quant" in by_source
    assert by_source["quant"].eligible is True
    assert by_source["quant"].available is True
    assert by_source["quant"].estimated_yes_probability is not None
    assert result.independent_probability is not None


def test_quant_weight_reflects_its_own_confidence(storage: Storage, monkeypatch) -> None:
    """Two otherwise-identical quant-routed markets, one with a decisive
    z-score (short horizon / far from threshold -> already-crossed, high
    confidence=75) and one with an ambiguous z-score close to threshold
    (lower confidence) must get DIFFERENT ensemble weights for the quant
    submodel — not the same fixed constant."""
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.resolve_coingecko_id", lambda asset: "bitcoin"
    )
    _seed_history(storage, n_yes=15, n_no=5, category="CRYPTO", question_prefix="crypto-hist")
    question = "Will Bitcoin reach $40,000 by December 31, 2030?"
    resolution = "Resolves YES if BTC reaches $40,000 by December 31, 2030."

    # High-confidence case: already crossed (confidence=75 in quant.py).
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.fetch_price_and_volatility",
        lambda coingecko_id: PriceData(current_price=50000.0, daily_volatility=0.03, days_of_history=90),
    )
    high_conf_result = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "crypto", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text=resolution,
    )

    # Low-confidence case: z close to 0 (current price barely below
    # threshold, small |z| with high volatility -> quant.py's lower-
    # confidence branch).
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.fetch_price_and_volatility",
        lambda coingecko_id: PriceData(current_price=39999.0, daily_volatility=0.5, days_of_history=90),
    )
    low_conf_result = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "crypto", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text=resolution,
    )

    high_conf_weight = next(
        s.weight for s in high_conf_result.submodel_estimates if s.name == "quant"
    )
    low_conf_weight = next(
        s.weight for s in low_conf_result.submodel_estimates if s.name == "quant"
    )
    assert high_conf_weight != low_conf_weight
    assert high_conf_weight > low_conf_weight


# ---------------------------------------------------------------------------
# F2 — evidence weighting reflects source-quality / confirmation-count
# ---------------------------------------------------------------------------

def test_evidence_weight_reflects_confirmation_count_and_source_quality(tmp_path: Path) -> None:
    """Two fixtures with the same submodels available (history + evidence)
    but different evidence quality signals (more confirming domains, higher
    link confidence) must produce a different weight for
    independent_evidence — not an equal split, and not the old flat 0.45
    constant regardless of evidence quality."""
    now = datetime.now(UTC)
    question = "Will Team Alpha win the qualifier?"
    resolution = "Resolves YES if Team Alpha wins the qualifier, NO otherwise."

    weak_storage = Storage(tmp_path / "weak.db")
    _seed_history(weak_storage, n_yes=15, n_no=5, category="OTHER", question_prefix="esports-hist")
    _link_evidence(
        weak_storage, "polymarket", "m1", question,
        [("Team Alpha wins the qualifier", "smallblog", "https://smallblog.example/1", 0.5)],
        now=now,
    )
    weak_result = compute_prediction(
        weak_storage.connection, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text=resolution,
    )
    weak_storage.close()

    strong_storage = Storage(tmp_path / "strong.db")
    _seed_history(strong_storage, n_yes=15, n_no=5, category="OTHER", question_prefix="esports-hist")
    _link_evidence(
        strong_storage, "polymarket", "m1", question,
        [
            ("Team Alpha wins the qualifier decisively", "reuters", "https://reuters.com/a", 0.95),
            ("Alpha confirmed as qualifier winner", "apnews", "https://apnews.com/b", 0.9),
            ("Official results: Alpha wins qualifier", "bbc", "https://bbc.com/c", 0.92),
        ],
        now=now,
    )
    strong_result = compute_prediction(
        strong_storage.connection, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text=resolution,
    )
    strong_storage.close()

    weak_evidence = next(s for s in weak_result.submodel_estimates if s.name == "independent_evidence")
    strong_evidence = next(s for s in strong_result.submodel_estimates if s.name == "independent_evidence")

    # The weak fixture has only 1 evidence item -> below
    # MIN_EVIDENCE_ITEMS_FOR_ESTIMATE (2), so it's simply unavailable
    # (weight 0). The strong fixture has 3 well-sourced, multiply-
    # confirming items and should be available with a real nonzero,
    # quality-derived weight — not the same weight the weak one would have
    # gotten had it merely cleared the availability bar.
    assert weak_evidence.available is False
    assert weak_evidence.weight == 0.0
    assert strong_evidence.available is True
    assert strong_evidence.weight > 0.0


# ---------------------------------------------------------------------------
# F3 — independent_probability is strictly market-blind (the critical test)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("market_yes_price", [0.05, 0.25, 0.50, 0.75, 0.95])
def test_independent_probability_market_blind_history_only(tmp_path: Path, market_yes_price) -> None:
    storage = Storage(tmp_path / f"hist-{market_yes_price}.db")
    _seed_history(storage, n_yes=12, n_no=8, category="esports", question_prefix="esports-hist")
    baseline = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True,
    )
    varied = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "esports", market_yes_price, 100000, 90, 0, None, True,
    )
    storage.close()
    assert varied.independent_probability == baseline.independent_probability


def test_independent_probability_market_blind_politics_routed(tmp_path: Path) -> None:
    question = "Will the Prime Minister resign before the end of the year?"
    resolution = "Resolves YES if the Prime Minister resigns, NO otherwise."
    results = []
    for i, price in enumerate((0.05, 0.25, 0.50, 0.75, 0.95)):
        storage = Storage(tmp_path / f"politics-{i}.db")
        _seed_history(storage, n_yes=10, n_no=5, category="GEOPOLITICS", question_prefix="politics-hist")
        results.append(
            compute_prediction(
                storage.connection, "m1", "polymarket", "m1", "politics", price, 100000, 90, 0, None, True,
                question=question, resolution_text=resolution,
            )
        )
        storage.close()
    independents = {r.independent_probability for r in results}
    assert len(independents) == 1


def test_independent_probability_market_blind_quant_routed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.resolve_coingecko_id", lambda asset: "bitcoin"
    )
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.fetch_price_and_volatility",
        lambda coingecko_id: PriceData(current_price=45000.0, daily_volatility=0.04, days_of_history=90),
    )
    question = "Will Bitcoin reach $40,000 by December 31, 2030?"
    resolution = "Resolves YES if BTC reaches $40,000 by December 31, 2030."
    results = []
    for i, price in enumerate((0.05, 0.25, 0.50, 0.75, 0.95)):
        storage = Storage(tmp_path / f"quant-{i}.db")
        _seed_history(
            storage, n_yes=10, n_no=5, category="CRYPTO", question_prefix="crypto-hist",
            event_type="price_above",
        )
        results.append(
            compute_prediction(
                storage.connection, "m1", "polymarket", "m1", "crypto", price, 100000, 90, 0, None, True,
                question=question, resolution_text=resolution,
            )
        )
        storage.close()
    independents = {r.independent_probability for r in results}
    assert len(independents) == 1  # byte-identical (exact equality) across all five market prices


def test_independent_probability_market_blind_evidence_based_claims_connected(
    tmp_path: Path,
) -> None:
    """Task 1 (claims.py wiring): compute_independent_evidence now also
    extracts+persists claims via extract_claim_from_event/
    group_claims_by_normalization as a side effect of scoring each linked
    article. That must remain purely additive — the evidence-route
    independent_probability itself must still be byte-identical across the
    full 5-point market-price sweep, exactly like the history/politics/
    quant routes above."""
    question = "Will Team Alpha win the qualifier?"
    resolution = "Resolves YES if Team Alpha wins the qualifier, NO otherwise."
    now = datetime.now(UTC)  # fixed across all 5 fixtures so recency_weight can't vary the result
    results = []
    for i, price in enumerate((0.05, 0.25, 0.50, 0.75, 0.95)):
        storage = Storage(tmp_path / f"evidence-claims-{i}.db")
        _seed_history(storage, n_yes=15, n_no=5, category="OTHER", question_prefix="esports-hist")
        _link_evidence(
            storage, "polymarket", "m1", question,
            [
                ("Team Alpha wins the qualifier decisively", "reuters", "https://reuters.com/a", 0.95),
                ("Alpha confirmed as qualifier winner", "apnews", "https://apnews.com/b", 0.9),
                ("Official results: Alpha wins qualifier", "bbc", "https://bbc.com/c", 0.92),
            ],
            now=now,
        )
        results.append(
            compute_prediction(
                storage.connection, "m1", "polymarket", "m1", "esports", price, 100000, 90, 0, None, True,
                question=question, resolution_text=resolution,
            )
        )
        storage.close()
    # Deliberately asserting on the *submodel-level* independent_evidence
    # estimate (pre-audit), not the top-level PredictionResult.independent_
    # probability: at extreme market prices (e.g. 0.05 vs. this fixture's
    # strong ~0.67 evidence estimate) Phase M's divergence_audit
    # legitimately REJECTs and suppresses the top-level field via its
    # model_disagreement check — which itself factors in the momentum
    # submodel, and momentum *is* allowed to depend on market price (that's
    # what momentum means). That's correct, price-dependent audit behavior,
    # not a violation of evidence-route market-blindness. The thing that
    # must never move with price is the evidence submodel's own estimate,
    # which is confirmed here.
    evidence_estimates = {
        next(s for s in r.submodel_estimates if s.name == "independent_evidence").estimated_yes_probability
        for r in results
    }
    assert len(evidence_estimates) == 1  # byte-identical across all five market prices
    for r in results:
        evidence_submodel = next(s for s in r.submodel_estimates if s.name == "independent_evidence")
        assert evidence_submodel.available is True


# ---------------------------------------------------------------------------
# F5 — market ineligible for any specialized model still falls back cleanly
# ---------------------------------------------------------------------------

def test_ineligible_market_falls_back_to_history_evidence_only(storage: Storage) -> None:
    # Part 1/4 correctness fix (2026-08): "win the qualifier" doesn't match
    # any recognized event_type vocabulary (semantics.py's sport pattern
    # only matches "win the tournament"/"championship"/etc — and every
    # event_type the parser DOES recognize maps to some specialized model
    # per specialized_router._EVENT_TYPE_TO_MODEL, so there is no longer a
    # fixture that is both "specialized-model-ineligible" AND
    # "proposition-parseable"). This is exactly the reported-bug shape:
    # History as the ONLY contributing submodel with an unparseable target
    # proposition (event_type=None). Part 4's explicit history-only safety
    # rule now correctly demotes this to NO_FORECAST rather than publishing
    # a quantitative independent_probability from History alone — this test
    # now asserts THAT is what happens, instead of the old (incorrect)
    # expectation that History alone could carry a real forecast here.
    _seed_history(storage, n_yes=15, n_no=5, category="OTHER", question_prefix="esports-hist")
    question = "Will Team Alpha win the qualifier?"
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "esports", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text="Resolves YES if Team Alpha wins.",
    )
    by_source = {c.source: c for c in result.contribution_breakdown}
    for specialized_name in ("quant", "macro", "politics", "geopolitics", "sports"):
        assert by_source[specialized_name].eligible is False
        assert by_source[specialized_name].available is False
    assert result.independent_probability is None
    assert result.forecast_status == "NO_FORECAST"
    assert result.recommendation != "STRONG_YES"
    assert result.recommendation != "STRONG_NO"


# ---------------------------------------------------------------------------
# F4 — contribution_breakdown distinguishes eligible-but-unavailable from
# not-eligible-for-this-market-type
# ---------------------------------------------------------------------------

def test_contribution_breakdown_distinguishes_eligibility_states(storage: Storage, monkeypatch) -> None:
    """A quant-eligible market where CoinGecko data is unavailable (quant
    itself returns unavailable) must show eligible=True, available=False —
    distinct from the other 4 specialized models, which are simply not
    eligible for a price-threshold event_type (eligible=False)."""
    monkeypatch.setattr(
        "polymarketpulse.prediction.engine.resolve_coingecko_id", lambda asset: None
    )
    _seed_history(storage, n_yes=15, n_no=5, category="CRYPTO", question_prefix="crypto-hist")
    question = "Will Bitcoin reach $40,000 by December 31, 2030?"
    result = compute_prediction(
        storage.connection, "m1", "polymarket", "m1", "crypto", 0.5, 100000, 90, 0, None, True,
        question=question, resolution_text="Resolves YES if BTC reaches $40,000 by December 31, 2030.",
    )
    by_source = {c.source: c for c in result.contribution_breakdown}
    assert by_source["quant"].eligible is True
    assert by_source["quant"].available is False
    for other in ("macro", "politics", "geopolitics", "sports"):
        assert by_source[other].eligible is False
        assert by_source[other].available is False


# ---------------------------------------------------------------------------
# No submodel gets weight purely for returning a number
# ---------------------------------------------------------------------------

def test_specialized_model_weight_is_zero_when_confidence_is_zero() -> None:
    assert quality_scaled_weight(0.45, 0.0 / 100.0) == 0.0
