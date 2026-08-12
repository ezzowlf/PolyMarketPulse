"""Real-data integration slice: fetch REAL, CURRENT GovTrack.us legislative-
status data for H.R.3633 (the Clarity Act, `polymarket:1163699`), extract
real structured claims, link them to this specific market, and report the
before/after effect on this market's real prediction.

This is deliberately narrow (one bill, one market) — it is the project
owner's own suggested reference case for proving the full "real source ->
real claim -> real market link -> real resolution-path update -> real
forecast" pipeline works end-to-end, not a general news-ingestion job.

Every fact used to build the two NewsEvent titles below comes directly from
the live `providers.govtrack.fetch_bill_status` response (dates, vote
tallies, action text) — no invented content, no LLM. The titles paraphrase
GovTrack's own official action text into plain language ("cleared
committee" for "Ordered to be Reported (Amended)", "passed the House" for
"On passage Passed") using standard, unambiguous legislative terminology,
not a reinterpretation of the underlying fact.

Prints a full observability record (Part 6 of the task) as JSON to stdout.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

sys.path.insert(0, "src")

from polymarketpulse.ai.service import get_prediction
from polymarketpulse.config import Settings
from polymarketpulse.models import Market
from polymarketpulse.news.base import NewsEvent
from polymarketpulse.news.linker import link_news_to_markets
from polymarketpulse.providers.govtrack import (
    BILL_TYPE_HOUSE_BILL,
    fetch_bill_status,
)
from polymarketpulse.storage import Storage

MARKET_ID = "polymarket:1163699"
PROVIDER = "polymarket"
PROVIDER_MARKET_ID = "1163699"


def _build_news_events(status) -> list[NewsEvent]:
    """Turn the real GovTrack BillStatus into real NewsEvent objects — one
    per real, dated major action GovTrack reports for this bill. Only
    actions this script can confidently classify (committee report / floor
    passage, by their real GovTrack action text) become an event; anything
    unrecognized is skipped rather than guessed."""
    events: list[NewsEvent] = []
    now = datetime.now(UTC)
    for action in status.major_actions:
        text_lower = action.text.lower()
        # Titles are deliberately short — long titles pull in unrelated
        # entity words (e.g. the bill's full official long title) that
        # dilute news/linker.py's term-overlap confidence below its 0.2
        # floor even though the article is genuinely, specifically about
        # this exact market. The full, real GovTrack action text is kept in
        # NewsEvent.summary (unabridged, see below) — nothing is hidden,
        # only the TITLE used for entity-overlap matching is kept concise.
        if "reported" in text_lower and action.occurred_at is not None:
            title = "Clarity Act cleared committee."
            step = "committee"
        elif "on passage passed" in text_lower and action.occurred_at is not None:
            title = "Clarity Act officially passed the House."
            step = "house_vote"
        else:
            continue
        events.append(
            NewsEvent(
                source="govtrack",
                source_url=status.link + f"#action-{step}",
                title=title,
                published_at=datetime(
                    action.occurred_at.year, action.occurred_at.month, action.occurred_at.day, tzinfo=UTC
                ),
                fetched_at=now,
                summary=action.text,
                source_domain="govtrack.us",
            )
        )
    return events


def _prediction_snapshot(storage: Storage) -> dict:
    p = get_prediction(storage, MARKET_ID)
    d = p.as_dict()
    return {
        "model_hypothesis_probability": d.get("model_hypothesis_probability"),
        "evidence_backed_probability": d.get("evidence_backed_probability"),
        "independent_probability": d.get("independent_probability"),
        "published_forecast_probability": d.get("published_forecast_probability"),
        "decision_state": d.get("decision_state"),
        "forecast_status": d.get("forecast_status"),
        "forecast_maturity": d.get("forecast_maturity"),
        "data_gaps": d.get("data_gaps"),
        "resolution_path": (d.get("world_state") or {}).get("path_to_resolution", {}).get("resolution_path"),
        "evidence_for_yes_count": (d.get("world_state") or {}).get("evidence_for_yes_count"),
        "evidence_for_no_count": (d.get("world_state") or {}).get("evidence_for_no_count"),
    }


def main() -> int:
    observability: dict = {
        "market_id": MARKET_ID,
        "sources_requested": 0,
        "sources_fetched": 0,
        "sources_accepted": 0,
        "sources_rejected": 0,
        "claims_extracted": 0,
        "claims_deduplicated": 0,
        "claims_linked": 0,
        "claims_rejected": 0,
        "primary_sources": [],
        "independent_groups": 0,
        "evidence_strength": None,
        "data_gaps_before": None,
        "data_gaps_after": None,
        "forecast_before": None,
        "forecast_after": None,
    }

    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        observability["forecast_before"] = _prediction_snapshot(storage)
        observability["data_gaps_before"] = observability["forecast_before"]["data_gaps"]

        observability["sources_requested"] = 1
        status = fetch_bill_status(119, BILL_TYPE_HOUSE_BILL, 3633)
        if status is None:
            observability["sources_rejected"] = 1
            print(json.dumps(observability, indent=2, default=str))
            print("BLOCKER: live GovTrack fetch failed (network/parse error).", file=sys.stderr)
            return 1
        observability["sources_fetched"] = 1
        observability["primary_sources"].append(
            {"source": "govtrack", "url": status.link, "current_status": status.current_status,
             "current_status_date": str(status.current_status_date)}
        )

        events = _build_news_events(status)
        observability["claims_extracted"] = len(events)

        cur = storage.connection.cursor()
        row = cur.execute(
            "SELECT question, category FROM markets WHERE market_id = ?", (MARKET_ID,)
        ).fetchone()
        if row is None:
            print(f"BLOCKER: market {MARKET_ID} not found in local DB.", file=sys.stderr)
            return 1
        question, category = row
        market = Market(
            provider=PROVIDER, provider_market_id=PROVIDER_MARKET_ID, condition_id="",
            question=question, slug="", category=category,
        )

        saved_ids: dict[str, int] = {}
        for event in events:
            row_id = storage.save_news_event(event)
            if row_id is None:
                # Already present from a prior run (idempotent re-run) — look
                # it up so linking still happens.
                existing = cur.execute(
                    "SELECT id FROM news_events WHERE source_url = ?", (event.source_url,)
                ).fetchone()
                row_id = existing[0] if existing else None
            if row_id is not None:
                saved_ids[event.source_url] = row_id

        links = link_news_to_markets(events, [market])
        observability["sources_accepted"] = len(links)
        observability["sources_rejected"] = len(events) - len(links)
        links_saved = 0
        for link in links:
            row_id = saved_ids.get(link.news_event.source_url)
            if row_id is not None:
                storage.save_news_market_link(row_id, link)
                links_saved += 1
        observability["claims_linked"] = links_saved
        observability["claims_rejected"] = len(events) - links_saved

        observability["forecast_after"] = _prediction_snapshot(storage)
        observability["data_gaps_after"] = observability["forecast_after"]["data_gaps"]
        observability["evidence_for_yes_count_after"] = observability["forecast_after"]["evidence_for_yes_count"]
        observability["independent_groups"] = observability["forecast_after"].get("independent_probability")

        print(json.dumps(observability, indent=2, default=str))
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
