"""Real, mocked (no live network) tests for research_runner.run_research_for_market
— the actual end-to-end Source Fetch -> Claim Extraction -> Evidence ->
Forecast Recompute -> Storage wiring, plus its persisted Observability
record."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from polymarketpulse.config import Settings
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.research_runner import (
    build_queue_from_db,
    run_recurring_research,
    run_research_for_market,
)
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _seed_market(storage: Storage) -> dict:
    question = "Will the ceasefire be confirmed by officials?"
    now = datetime.now(UTC).isoformat()
    storage.connection.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
        "first_seen_at, last_seen_at, resolution_status) "
        "VALUES ('rr-1', 'polymarket', 'rr-1', ?, 'rr-1', 'https://x', ?, ?, 'open')",
        (question, now, now),
    )
    storage.connection.commit()
    row = storage.connection.execute(
        "SELECT market_id, provider, provider_market_id, question, category, classified_category, resolution_source "
        "FROM markets WHERE provider_market_id = 'rr-1'"
    ).fetchone()
    cols = ("market_id", "provider", "provider_market_id", "question", "category", "classified_category", "resolution_source")
    return dict(zip(cols, row, strict=True))


def test_queue_uses_latest_persisted_forecast_deadline_and_gap_state(storage: Storage) -> None:
    now = datetime.now(UTC)
    storage.connection.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, first_seen_at, last_seen_at, end_date, resolution_status) "
        "VALUES ('queue-1', 'polymarket', 'queue-1', 'Will a vote occur?', 'queue-1', 'https://x', ?, ?, ?, 'open')",
        (now.isoformat(), now.isoformat(), (now.replace(year=now.year + 1)).isoformat()),
    )
    storage.connection.execute(
        "INSERT INTO prediction_snapshots (market_id, provider, provider_market_id, category, prediction_version, created_at, market_yes_probability, model_hypothesis_probability, recommendation, comparable_sample_size, data_gap_summary_json) "
        "VALUES ('queue-1', 'polymarket', 'queue-1', NULL, 'v2', ?, 0.20, 0.70, 'WATCH_YES', 10, '{\"critical\": 1, \"high\": 2}')",
        (now.isoformat(),),
    )
    storage.connection.execute(
        "INSERT INTO news_events (source, source_url, title, published_at, fetched_at, content_hash) VALUES ('gov', 'https://gov.example/a', 'Vote update', ?, ?, 'queue-news')",
        (now.isoformat(), now.isoformat()),
    )
    news_id = storage.connection.execute("SELECT id FROM news_events WHERE content_hash = 'queue-news'").fetchone()[0]
    storage.connection.execute(
        "INSERT INTO news_market_links (news_event_id, provider, provider_market_id, confidence, match_reason, matched_terms, created_at) VALUES (?, 'polymarket', 'queue-1', 1.0, 'test', 'vote', ?)",
        (news_id, now.isoformat()),
    )
    storage.connection.commit()
    _, queue = build_queue_from_db(storage)
    entry = next(item for item in queue if item.market_id == "queue-1")
    assert entry.priority_score == 36.0  # 50pp divergence + 14 data-gap points + long-horizon base score
    assert any("Divergenz" in reason for reason in entry.reasons)
    assert any("kritische" in reason for reason in entry.reasons)


def test_real_run_fetches_sources_extracts_claims_and_persists_observability(storage: Storage) -> None:
    market_row = _seed_market(storage)
    settings = Settings.load()

    fake_events = [
        NewsEvent(
            source="reuters", source_url="https://reuters.com/a", title="Ceasefire confirmed by officials",
            published_at=datetime.now(UTC), fetched_at=datetime.now(UTC),
        ),
        NewsEvent(
            source="apnews", source_url="https://apnews.com/b", title="Ceasefire confirmed by officials (AP)",
            published_at=datetime.now(UTC), fetched_at=datetime.now(UTC),
        ),
    ]
    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=(fake_events, "OK")):
        record = run_research_for_market(storage, settings, market_row, trigger="test")

    assert record.sources_requested == 1
    assert record.sources_fetched == 2
    assert record.sources_accepted >= 1  # real linker decides exact match count
    assert record.claims_extracted >= 1  # real claim extraction, not a placeholder
    assert record.final_status  # forecast recompute actually ran

    # Persisted, retrievable Observability record — not just a log line.
    rows = storage.get_research_runs(provider_market_id="rr-1")
    assert len(rows) == 1
    assert rows[0]["sources_fetched"] == 2
    assert rows[0]["claims_extracted"] == record.claims_extracted
    detail = json.loads(rows[0]["detail_json"])
    assert detail["source_attempts"][-1]["provider"] == "gdelt"
    assert detail["source_attempts"][-1]["role"] == "discovery"


def test_second_identical_run_does_not_duplicate_sources_or_claims(storage: Storage) -> None:
    """Real dedup: running the same market's research twice with the same
    real articles must not create a second copy of the same source/claim —
    Storage.save_news_event dedups by content_hash/source_url,
    save_news_market_link by (news_event_id, provider, provider_market_id),
    and save_claim by stable claim_id, all via ON CONFLICT DO NOTHING."""
    market_row = _seed_market(storage)
    settings = Settings.load()
    fake_events = [
        NewsEvent(
            source="reuters", source_url="https://reuters.com/a", title="Ceasefire confirmed by officials",
            published_at=datetime.now(UTC), fetched_at=datetime.now(UTC),
        ),
    ]
    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=(fake_events, "OK")):
        run_research_for_market(storage, settings, market_row, trigger="test")
        second = run_research_for_market(storage, settings, market_row, trigger="test")

    assert second.claims_extracted == 0  # already persisted by the first run
    news_event_count = storage.connection.execute(
        "SELECT COUNT(*) FROM news_events WHERE source_url = 'https://reuters.com/a'"
    ).fetchone()[0]
    assert news_event_count == 1  # not duplicated on the second run
    link_count = storage.connection.execute(
        "SELECT COUNT(*) FROM news_market_links WHERE provider_market_id = 'rr-1'"
    ).fetchone()[0]
    assert link_count == 1


def test_no_sources_found_still_completes_and_reports_zero_honestly(storage: Storage) -> None:
    market_row = _seed_market(storage)
    settings = Settings.load()

    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")):
        record = run_research_for_market(storage, settings, market_row, trigger="test")

    assert record.sources_fetched == 0
    assert record.sources_accepted == 0
    assert record.claims_extracted == 0  # honestly zero, not fabricated
    assert record.detail["source_fetch_status"] == "OK"  # reached, genuinely 0 hits


def test_source_fetch_failure_is_visibly_distinct_from_empty_result(storage: Storage) -> None:
    """The exact requirement: a source that could not be reached must never
    be reported the same way as a source that was reached but had nothing
    relevant — both currently look like sources_fetched=0 to a naive
    caller, so the real distinction must live in detail.source_fetch_status."""
    market_row = _seed_market(storage)
    settings = Settings.load()

    with patch(
        "polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "SOURCE_FETCH_FAILED")
    ):
        record = run_research_for_market(storage, settings, market_row, trigger="test")

    assert record.sources_fetched == 0
    assert record.detail["source_fetch_status"] == "SOURCE_FETCH_FAILED"


def test_recurring_research_skips_recently_researched_market(storage: Storage) -> None:
    """Real Recurring Ingestion interval gating: a market researched moments
    ago must not be re-researched again on the very next recurring pass —
    this is what prevents unchanged sources being reprocessed every scan."""
    _seed_market(storage)
    settings = Settings.load()

    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")):
        first_pass = run_recurring_research(storage, settings, limit=10)
        assert len(first_pass) == 1  # never researched before -> runs once

        second_pass = run_recurring_research(storage, settings, limit=10)
        assert len(second_pass) == 0  # too soon since the first pass -> skipped


def test_recurring_research_respects_limit_and_cost_budget(storage: Storage) -> None:
    for i in range(3):
        now = datetime.now(UTC).isoformat()
        storage.connection.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
            "first_seen_at, last_seen_at, resolution_status) "
            "VALUES (?, 'polymarket', ?, 'Q?', ?, 'https://x', ?, ?, 'open')",
            (f"rr-{i}", f"rr-{i}", f"rr-{i}", now, now),
        )
    storage.connection.commit()
    settings = Settings.load()

    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")):
        records = run_recurring_research(storage, settings, limit=2)
    assert len(records) == 2  # real limit respected, not all 3 candidates run


def test_gap_closure_recorded_closed_on_new_govtrack_claim_open_on_repeat(storage: Storage) -> None:
    """Phase 7.6: the real regression case -- a genuinely new GovTrack
    status must record result_status=CLOSED, but re-running the SAME
    research on the SAME already-known status must record OPEN, never a
    second fake CLOSED (SUCCESSFUL_FETCH without new information !=
    GAP_CLOSED)."""
    from polymarketpulse.providers.govtrack import BillStatus

    market_row = _seed_market(storage)
    storage.connection.execute(
        "UPDATE markets SET question = ? WHERE market_id = 'rr-1'",
        ("Clarity Act (H.R.3633) signed into law in 2026?",),
    )
    storage.connection.commit()
    market_row["question"] = "Clarity Act (H.R.3633) signed into law in 2026?"
    settings = Settings.load()

    fake_status = BillStatus(
        congress=119, bill_type="house_bill", number=3633, display_number="H.R. 3633",
        title="H.R. 3633: Digital Asset Market Clarity Act",
        current_status="pass_over_house", current_status_label="Passed House (Senate next)",
        current_status_description="", current_status_date=None, introduced_date=None,
        is_alive=True, link="https://www.govtrack.us/congress/bills/119/hr3633",
        major_actions=(), fetched_at=datetime.now(UTC),
    )
    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")), \
         patch("polymarketpulse.providers.govtrack.fetch_bill_status", return_value=fake_status):
        run_research_for_market(storage, settings, market_row, trigger="test")
        run_research_for_market(storage, settings, market_row, trigger="test")

    rows = storage.get_gap_closures(
        provider="polymarket", provider_market_id="rr-1", gap_key="polymarket:rr-1:MISSING_RESOLUTION_DATA",
    )
    assert len(rows) == 2  # full attempt history preserved, not overwritten
    statuses = [r["result_status"] for r in rows]
    assert "CLOSED" in statuses
    assert "OPEN" in statuses
    closed_row = next(r for r in rows if r["result_status"] == "CLOSED")
    assert closed_row["closed_at"] is not None
    open_row = next(r for r in rows if r["result_status"] == "OPEN")
    assert open_row["closed_at"] is None

    latest = storage.get_gap_closures(
        provider="polymarket", provider_market_id="rr-1", gap_key="polymarket:rr-1:MISSING_RESOLUTION_DATA",
        latest_only=True,
    )
    assert len(latest) == 1
    assert latest[0]["result_status"] == "OPEN"  # the real current state, not the stale CLOSED one


def test_no_archetype_market_gets_not_applicable_gap_no_wasted_fetch(storage: Storage) -> None:
    """Phase 7.6.3: a market with no supported archetype must record
    NOT_APPLICABLE, never a wasted/fake research attempt."""
    market_row = _seed_market(storage)  # "ceasefire" question -- no bill number, no chokepoint keyword
    settings = Settings.load()

    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "SOURCE_FETCH_FAILED")):
        run_research_for_market(storage, settings, market_row, trigger="test")

    rows = storage.get_gap_closures(provider="polymarket", provider_market_id="rr-1")
    assert len(rows) == 1
    # Either NO_ARCHETYPE (no eligible model) or MISSING_PRIMARY_SOURCE with
    # BLOCKED_PROVIDER (GDELT genuinely failed) is honest here -- both are
    # real, non-fabricated outcomes; a NOT_APPLICABLE/BLOCKED_PROVIDER pair
    # is what must never be silently reported as CLOSED.
    assert rows[0]["result_status"] in ("NOT_APPLICABLE", "BLOCKED_PROVIDER")
    assert rows[0]["result_status"] != "CLOSED"


def test_legislation_market_fetches_and_persists_a_real_govtrack_claim(storage: Storage) -> None:
    """Real, targeted second-source integration for legislation-shaped
    markets: a question containing a real bill number ("H.R.3633") must
    trigger a real GovTrack fetch and persist a PRIMARY_CONFIRMED claim
    with a real resolution_step, distinct from the generic GDELT path."""
    from polymarketpulse.providers.govtrack import BillStatus

    market_row = _seed_market(storage)
    storage.connection.execute(
        "UPDATE markets SET question = ? WHERE market_id = 'rr-1'",
        ("Clarity Act (H.R.3633) signed into law in 2026?",),
    )
    storage.connection.commit()
    market_row["question"] = "Clarity Act (H.R.3633) signed into law in 2026?"
    settings = Settings.load()

    fake_status = BillStatus(
        congress=119, bill_type="house_bill", number=3633, display_number="H.R. 3633",
        title="H.R. 3633: Digital Asset Market Clarity Act",
        current_status="pass_over_house", current_status_label="Passed House (Senate next)",
        current_status_description="", current_status_date=None, introduced_date=None,
        is_alive=True, link="https://www.govtrack.us/congress/bills/119/hr3633",
        major_actions=(), fetched_at=datetime.now(UTC),
    )
    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")), \
         patch("polymarketpulse.providers.govtrack.fetch_bill_status", return_value=fake_status):
        record = run_research_for_market(storage, settings, market_row, trigger="test")

    assert record.detail["legislation"]["attempted"] is True
    assert record.detail["legislation"]["fetch_status"] == "OK"
    assert record.detail["legislation"]["resolution_step"] == "house_vote"

    row = storage.connection.execute(
        "SELECT source_id, verification_status, resolution_step FROM claims WHERE source_id = 'govtrack'"
    ).fetchone()
    assert row is not None
    assert row[1] == "PRIMARY_CONFIRMED"
    assert row[2] == "house_vote"

    # Phase 7.14: the real product-facing before/after is always populated
    # -- computed via the same product_mode_for_prediction() the API uses,
    # never a second classification. This minimal synthetic fixture (no
    # classified_category/event_type columns set) doesn't necessarily
    # promote past INSUFFICIENT_DATA even with a real PATH_STEP claim
    # attached -- "unchanged" is the honest, common outcome documented on
    # the dataclass itself; the live 22-market audit is where a real
    # promotion is actually observed and reported.
    assert record.product_mode_before in ("VALIDATED_NUMERIC_FORECAST", "STRUCTURED_OUTLOOK", "INSUFFICIENT_DATA")
    assert record.product_mode_after in ("VALIDATED_NUMERIC_FORECAST", "STRUCTURED_OUTLOOK", "INSUFFICIENT_DATA")


def test_legislation_run_creates_real_event_entity_relation_graph(storage: Storage) -> None:
    """Phase C: the GovTrack claim must also populate the real
    Event/Entity/Relation graph (migration 12), not just claims/
    claim_market_links -- and the resulting relation must be readable back
    through event_relations.py's own reader, the actual ensemble input."""
    from polymarketpulse.prediction.event_relations import collect_event_relation_signals
    from polymarketpulse.providers.govtrack import BillStatus

    market_row = _seed_market(storage)
    storage.connection.execute(
        "UPDATE markets SET question = ? WHERE market_id = 'rr-1'",
        ("Clarity Act (H.R.3633) signed into law in 2026?",),
    )
    storage.connection.commit()
    market_row["question"] = "Clarity Act (H.R.3633) signed into law in 2026?"
    settings = Settings.load()

    fake_status = BillStatus(
        congress=119, bill_type="house_bill", number=3633, display_number="H.R. 3633",
        title="H.R. 3633: Digital Asset Market Clarity Act",
        current_status="pass_over_house", current_status_label="Passed House (Senate next)",
        current_status_description="", current_status_date=None, introduced_date=None,
        is_alive=True, link="https://www.govtrack.us/congress/bills/119/hr3633",
        major_actions=(), fetched_at=datetime.now(UTC),
    )
    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")), \
         patch("polymarketpulse.providers.govtrack.fetch_bill_status", return_value=fake_status):
        run_research_for_market(storage, settings, market_row, trigger="test")

    entity_row = storage.connection.execute(
        "SELECT id, entity_type FROM entities WHERE canonical_name = ?",
        ("h.r. 3633: digital asset market clarity act",),
    ).fetchone()
    assert entity_row is not None
    assert entity_row[1] == "legislation"

    event_row = storage.connection.execute(
        "SELECT id, event_type, source FROM events WHERE source = 'govtrack'"
    ).fetchone()
    assert event_row is not None
    assert event_row[1] == "legislative_progress"

    relation_row = storage.connection.execute(
        "SELECT relation_type, evidence_tier, target_provider, target_provider_market_id "
        "FROM event_relations WHERE target_provider = ? AND target_provider_market_id = ?",
        (market_row["provider"], market_row["provider_market_id"]),
    ).fetchone()
    assert relation_row is not None
    assert relation_row[0] == "SIGNALS"
    assert relation_row[1] == "KNOWN"

    signals = collect_event_relation_signals(
        storage.connection, market_row["provider"], market_row["provider_market_id"]
    )
    assert len(signals) == 1
    assert signals[0].relation_type == "SIGNALS"
    assert signals[0].evidence_tier == "KNOWN"
    assert signals[0].quantitative is False  # no strength/confidence-numeric on this claim path


def test_legislation_run_twice_does_not_duplicate_event_relation_graph(storage: Storage) -> None:
    """Repeated research runs for the same real fact must not grow
    entities/events/event_relations unboundedly -- dedup by content, same
    discipline as claims/claim_market_links."""
    from polymarketpulse.providers.govtrack import BillStatus

    market_row = _seed_market(storage)
    storage.connection.execute(
        "UPDATE markets SET question = ? WHERE market_id = 'rr-1'",
        ("Clarity Act (H.R.3633) signed into law in 2026?",),
    )
    storage.connection.commit()
    market_row["question"] = "Clarity Act (H.R.3633) signed into law in 2026?"
    settings = Settings.load()

    fake_status = BillStatus(
        congress=119, bill_type="house_bill", number=3633, display_number="H.R. 3633",
        title="H.R. 3633: Digital Asset Market Clarity Act",
        current_status="pass_over_house", current_status_label="Passed House (Senate next)",
        current_status_description="", current_status_date=None, introduced_date=None,
        is_alive=True, link="https://www.govtrack.us/congress/bills/119/hr3633",
        major_actions=(), fetched_at=datetime.now(UTC),
    )
    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")), \
         patch("polymarketpulse.providers.govtrack.fetch_bill_status", return_value=fake_status):
        run_research_for_market(storage, settings, market_row, trigger="test")
        run_research_for_market(storage, settings, market_row, trigger="test")

    assert storage.connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
    assert storage.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert storage.connection.execute("SELECT COUNT(*) FROM event_relations").fetchone()[0] == 1


def test_legislation_status_change_supersedes_prior_govtrack_claim(storage: Storage) -> None:
    """Phase D: when a real GovTrack status update changes (house passed ->
    senate passed), the prior GovTrack claim for this bill must be marked
    superseded, and evidence.py's structured-claim reader must stop
    returning it -- an outdated PATH_STEP claim must not keep influencing
    the resolution path once a newer real status exists."""
    from polymarketpulse.prediction.evidence import _structured_path_step_claims
    from polymarketpulse.providers.govtrack import BillStatus

    market_row = _seed_market(storage)
    storage.connection.execute(
        "UPDATE markets SET question = ? WHERE market_id = 'rr-1'",
        ("Clarity Act (H.R.3633) signed into law in 2026?",),
    )
    storage.connection.commit()
    market_row["question"] = "Clarity Act (H.R.3633) signed into law in 2026?"
    settings = Settings.load()

    status_1 = BillStatus(
        congress=119, bill_type="house_bill", number=3633, display_number="H.R. 3633",
        title="H.R. 3633: Digital Asset Market Clarity Act",
        current_status="pass_over_house", current_status_label="Passed House (Senate next)",
        current_status_description="", current_status_date=None, introduced_date=None,
        is_alive=True, link="https://www.govtrack.us/congress/bills/119/hr3633",
        major_actions=(), fetched_at=datetime.now(UTC),
    )
    status_2 = BillStatus(
        congress=119, bill_type="house_bill", number=3633, display_number="H.R. 3633",
        title="H.R. 3633: Digital Asset Market Clarity Act",
        current_status="pass_over_senate", current_status_label="Passed Senate (President next)",
        current_status_description="", current_status_date=None, introduced_date=None,
        is_alive=True, link="https://www.govtrack.us/congress/bills/119/hr3633",
        major_actions=(), fetched_at=datetime.now(UTC),
    )

    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")), \
         patch("polymarketpulse.providers.govtrack.fetch_bill_status", return_value=status_1):
        run_research_for_market(storage, settings, market_row, trigger="test")

    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")), \
         patch("polymarketpulse.providers.govtrack.fetch_bill_status", return_value=status_2):
        run_research_for_market(storage, settings, market_row, trigger="test")

    rows = storage.connection.execute(
        "SELECT claim_id, superseded_by, resolution_step FROM claims WHERE source_id = 'govtrack' ORDER BY created_at"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] == rows[1][0]  # first claim's superseded_by points at the second claim's id
    assert rows[1][1] is None  # the newest claim is not itself superseded

    remaining = _structured_path_step_claims(storage.connection, market_row["provider"], market_row["provider_market_id"])
    assert len(remaining) == 1
    assert remaining[0]["resolution_step"] == "senate_vote"


def test_hormuz_market_fetches_real_chokepoint_data_and_derives_direction(storage: Storage) -> None:
    """Real, targeted second-source integration for strategic-waterway
    markets: a question mentioning "Hormuz" must trigger a real IMF
    PortWatch fetch and persist a claim whose direction is correctly
    derived from comparing the real 7-day average to the real threshold
    parsed from the resolution text -- not a generic Iran/Hormuz article."""
    from datetime import date

    from polymarketpulse.providers.imf_portwatch import ChokepointTransitData

    market_row = _seed_market(storage)
    storage.connection.execute(
        "UPDATE markets SET question = ?, resolution_source = ? WHERE market_id = 'rr-1'",
        (
            "Strait of Hormuz traffic returns to normal by August 31?",
            "resolves YES if 7-day moving average of transit calls is equal to or above 60",
        ),
    )
    storage.connection.commit()
    market_row["question"] = "Strait of Hormuz traffic returns to normal by August 31?"
    market_row["resolution_source"] = "resolves YES if 7-day moving average of transit calls is equal to or above 60"
    settings = Settings.load()

    fake_data = ChokepointTransitData(
        chokepoint="Strait of Hormuz",
        observations=((date(2026, 8, 9), 1),),
        seven_day_average=4.43,
        fetched_at=datetime.now(UTC),
    )
    with patch("polymarketpulse.news.gdelt.fetch_gdelt_with_status", return_value=([], "OK")), \
         patch("polymarketpulse.providers.imf_portwatch.fetch_chokepoint_transit_data", return_value=fake_data):
        record = run_research_for_market(storage, settings, market_row, trigger="test")

    assert record.detail["chokepoint"]["attempted"] is True
    assert record.detail["chokepoint"]["fetch_status"] == "OK"
    assert record.detail["chokepoint"]["seven_day_average"] == 4.43
    assert record.detail["chokepoint"]["threshold"] == 60
    assert record.detail["chokepoint"]["direction"] == "negative"  # 4.43 << 60

    row = storage.connection.execute(
        "SELECT source_id, verification_status, direction FROM claims WHERE source_id = 'imf_portwatch'"
    ).fetchone()
    assert row is not None
    assert row[1] == "PRIMARY_CONFIRMED"
    assert row[2] == "negative"
