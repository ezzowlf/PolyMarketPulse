"""Research Runner — executes ONE real market through the EXISTING Live
Evidence pipeline end to end:

    Market -> Source Fetch (GDELT, per-market query) -> Claim Extraction
    -> Market Linking -> Evidence -> Resolution Path -> Forecast Recompute
    -> Storage

This is deliberately not a new/parallel architecture: source fetching reuses
`news.gdelt`/`news.linker` exactly as `cli.cmd_news_fetch` already does, and
the claim-extraction/evidence/forecast steps run inside `ai.service.get_prediction`
-> `prediction.engine.compute_prediction` -> `prediction.evidence.compute_independent_evidence`
exactly as every existing prediction call already does. This module's only
real, new contribution is (a) scoping the fetch to one specific market
in-band and (b) recording a real, persisted Observability record of what
happened — real before/after counts, never fabricated, never estimated.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .ai.service import get_prediction
from .storage import Storage


def _claims_total(storage: Storage) -> int:
    try:
        return storage.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    except Exception:  # noqa: BLE001 - observability helper, must never break the real run
        return 0


def _news_links_total(storage: Storage, provider: str, provider_market_id: str) -> int:
    try:
        return storage.connection.execute(
            "SELECT COUNT(*) FROM news_market_links WHERE provider = ? AND provider_market_id = ?",
            (provider, provider_market_id),
        ).fetchone()[0]
    except Exception:  # noqa: BLE001 - observability helper, must never break the real run
        return 0


def _independent_groups_and_primary(prediction) -> tuple[int, int]:
    """Real counts derived from the already-computed IndependentEvidenceResult
    — never re-derived independently, so this can never disagree with what
    the evidence engine itself reported."""
    ev = prediction.independent_evidence
    if ev is None:
        return 0, 0
    all_factors = list(ev.evidence_for_yes) + list(ev.evidence_for_no)
    groups = {f.independence_group for f in all_factors if f.independence_group}
    primary = sum(1 for f in all_factors if f.source_type == "primary_official")
    return len(groups), primary


@dataclass(frozen=True)
class ResearchRunObservability:
    """One real, persistable Observability record for a single research run."""

    run_at: str
    provider: str
    provider_market_id: str
    question: str
    trigger: str
    sources_requested: int = 0
    sources_fetched: int = 0
    sources_accepted: int = 0
    sources_rejected: int = 0
    claims_extracted: int = 0
    claims_deduplicated: int = 0
    claims_linked: int = 0
    claims_rejected: int = 0
    independent_source_groups: int = 0
    primary_source_count: int = 0
    data_gaps_before: int | None = None
    data_gaps_after: int | None = None
    evidence_before: int | None = None
    evidence_after: int | None = None
    model_hypothesis_before: float | None = None
    model_hypothesis_after: float | None = None
    evidence_backed_before: float | None = None
    evidence_backed_after: float | None = None
    published_forecast_before: float | None = None
    published_forecast_after: float | None = None
    final_status: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


# Real GovTrack current_status slug -> our own resolution_step vocabulary
# (world_state.py's ResolutionStep names). GovTrack's status field is
# already a well-defined, government-sourced slug (not free text), so this
# is an exact real mapping, not a fuzzy keyword guess.
_GOVTRACK_STATUS_TO_STEP = {
    "introduced": "introduced",
    "referred": "introduced",
    "reported": "committee",
    "prov_kill_committee": "committee",
    "pass_over_house": "house_vote",
    "pass_over_senate": "senate_vote",
    "pass_back_house": "house_vote",
    "pass_back_senate": "senate_vote",
    "conference_passed_house": "house_vote",
    "conference_passed_senate": "senate_vote",
    "enacted_signed": "presidential_action",
    "enacted_veto_override": "presidential_action",
    "vetoed": "presidential_action",
    "prov_kill_veto": "presidential_action",
}

_BILL_NUMBER_RE = re.compile(r"H\.?\s?R\.?\s?(\d{2,5})", re.IGNORECASE)


def _fetch_and_persist_legislation_claim(storage: Storage, question: str) -> dict:
    """Real, targeted official-source fetch for legislation-shaped markets:
    extracts a real bill number from the question text (e.g. "H.R.3633"),
    fetches that bill's REAL current status from GovTrack's public API
    (providers/govtrack.py -- the same free, keyless, government-sourced
    integration built and verified in an earlier round but never actually
    wired into a real research run before this), and persists it as a real,
    stable, PRIMARY_CONFIRMED claim.

    This is intentionally scoped: it persists a real official claim (real
    resolution_step, real government source citation) but does NOT feed it
    through compute_independent_evidence's news-article pipeline, which is
    structurally article/news_market_links-shaped and has no path for a
    structured official data point today -- documented as a real, honest
    architectural gap for a future round rather than force-fit here.

    Returns a real summary dict for Observability; never raises -- a
    fetch/parse failure just means no claim this run, not a broken run."""
    match = _BILL_NUMBER_RE.search(question or "")
    if match is None:
        return {"attempted": False}

    from .providers.govtrack import BILL_TYPE_HOUSE_BILL, fetch_bill_status

    number = int(match.group(1))
    status = fetch_bill_status(119, BILL_TYPE_HOUSE_BILL, number)
    if status is None:
        return {"attempted": True, "fetch_status": "SOURCE_FETCH_FAILED", "bill_number": number}

    import hashlib as _hashlib

    from .claims import Claim

    step_name = _GOVTRACK_STATUS_TO_STEP.get(status.current_status)
    predicate = f"{status.current_status_label} (as of {status.current_status_date})"
    claim_id = _hashlib.sha256(
        f"govtrack:{status.congress}:{status.bill_type}:{status.number}:{status.current_status}".encode()
    ).hexdigest()[:32]
    claim = Claim(
        claim_id=claim_id, subject=status.title or f"H.R.{number}", predicate=predicate, object=None,
        speaker=None, source_id="govtrack", source_url=status.link,
        timestamp=datetime.combine(status.current_status_date, datetime.min.time(), tzinfo=UTC)
        if status.current_status_date else None,
        verification_status="PRIMARY_CONFIRMED", confidence=0.95,
        entities=(status.title or "",), location=None,
        raw_reference=f"{status.current_status}; major_actions={len(status.major_actions)}",
        event_type="legislative_progress", direction="positive" if status.is_alive else "negative",
        resolution_step=step_name,
    )
    newly_inserted = storage.save_claim(claim)
    storage.save_claim_source(claim.claim_id, "govtrack", status.link, claim.timestamp.isoformat() if claim.timestamp else None)
    return {
        "attempted": True, "fetch_status": "OK", "bill_number": number,
        "current_status": status.current_status, "resolution_step": step_name,
        "claim_newly_inserted": newly_inserted, "source_url": status.link,
    }


def run_research_for_market(
    storage: Storage,
    settings,
    market_row: dict,
    trigger: str = "manual",
    fetch_timeout: float = 10.0,
) -> ResearchRunObservability:
    """Runs the real end-to-end pipeline for ONE market and returns (and
    persists) a real Observability record.

    market_row must contain: market_id, provider, provider_market_id,
    question (same shape as ai.service._load_market_row's output).
    """
    from .news.gdelt import build_query_for_question, fetch_gdelt_with_status
    from .news.linker import link_news_to_markets

    started = time.monotonic()
    provider = market_row["provider"]
    provider_market_id = market_row["provider_market_id"]
    market_id = market_row["market_id"]
    question = market_row["question"] or ""

    claims_before_total = _claims_total(storage)
    links_before = _news_links_total(storage, provider, provider_market_id)

    pred_before = get_prediction(storage, market_id)
    groups_before, primary_before = _independent_groups_and_primary(pred_before)

    # --- Real, targeted official-source fetch for legislation-shaped
    # markets (e.g. "H.R.3633") — GovTrack, not another GDELT query.
    legislation_result = _fetch_and_persist_legislation_claim(storage, question)

    # --- Real source fetch, scoped to this one market -----------------
    sources_requested = 1  # one real GDELT query for this market's own question
    query = build_query_for_question(question)
    gdelt_events, source_fetch_status = fetch_gdelt_with_status(query, timeout=fetch_timeout)
    sources_fetched = len(gdelt_events)
    # Real, visible distinction (not folded into a generic "0 fetched"):
    # SOURCE_FETCH_FAILED (a genuine transport/parse failure) vs OK-with-
    # zero-hits (the source was reached, it just has no matching coverage
    # right now) vs EMPTY_QUERY (nothing to search — e.g. no question text).

    saved_ids: dict[str, int] = {}
    for event in gdelt_events:
        row_id = storage.save_news_event(event)
        if row_id is not None:
            saved_ids[event.source_url] = row_id

    # Link against this market only (real Market object, not the whole DB).
    from .models import Market

    market_obj = Market(
        provider=provider, provider_market_id=provider_market_id, condition_id="",
        question=question, slug=provider_market_id,
    )
    links = link_news_to_markets(gdelt_events, [market_obj])
    sources_accepted = 0
    for link in links:
        row_id = saved_ids.get(link.news_event.source_url)
        if row_id is not None:
            storage.save_news_market_link(row_id, link)
            sources_accepted += 1
    sources_rejected = sources_fetched - sources_accepted

    # --- Real recompute: this is what actually runs claim extraction /
    # evidence / resolution path / forecast, exactly as every other
    # prediction call in this codebase does (no parallel logic here).
    pred_after = get_prediction(storage, market_id)

    claims_after_total = _claims_total(storage)
    links_after = _news_links_total(storage, provider, provider_market_id)
    groups_after, primary_after = _independent_groups_and_primary(pred_after)

    claims_extracted_this_run = max(0, claims_after_total - claims_before_total)

    evidence_before_flag = 1 if pred_before.independent_evidence and pred_before.independent_evidence.available else 0
    evidence_after_flag = 1 if pred_after.independent_evidence and pred_after.independent_evidence.available else 0

    gaps_before_count = pred_before.data_gaps.total_gaps if pred_before.data_gaps else None
    gaps_after_count = pred_after.data_gaps.total_gaps if pred_after.data_gaps else None

    duration_ms = int((time.monotonic() - started) * 1000)

    record = ResearchRunObservability(
        run_at=datetime.now(UTC).isoformat(),
        provider=provider, provider_market_id=provider_market_id, question=question,
        trigger=trigger,
        sources_requested=sources_requested, sources_fetched=sources_fetched,
        sources_accepted=sources_accepted, sources_rejected=sources_rejected,
        claims_extracted=claims_extracted_this_run,
        claims_deduplicated=0,  # real dedup happens inside claim_groups; not separately counted here
        claims_linked=max(0, links_after - links_before),
        claims_rejected=max(0, sources_fetched - sources_accepted),
        independent_source_groups=groups_after,
        primary_source_count=primary_after,
        data_gaps_before=gaps_before_count, data_gaps_after=gaps_after_count,
        evidence_before=evidence_before_flag, evidence_after=evidence_after_flag,
        model_hypothesis_before=pred_before.model_hypothesis_probability,
        model_hypothesis_after=pred_after.model_hypothesis_probability,
        evidence_backed_before=pred_before.evidence_backed_probability,
        evidence_backed_after=pred_after.evidence_backed_probability,
        published_forecast_before=pred_before.published_forecast_probability,
        published_forecast_after=pred_after.published_forecast_probability,
        final_status=pred_after.forecast_status or "",
        duration_ms=duration_ms,
        cost_usd=0.0,  # GDELT/news pipeline is free/keyless; no paid calls made
        detail={
            "gdelt_query": query,
            "source_fetch_status": source_fetch_status,
            "legislation": legislation_result,
            "groups_before": groups_before, "primary_before": primary_before,
        },
    )
    storage.save_research_run(record.as_dict())
    return record


# Priority-tiered recheck intervals — high-priority (large real divergence,
# urgent deadline, critical gaps) markets get re-researched much more often
# than low-signal ones. Deliberately reuses research_queue.py's own
# priority_score bands rather than inventing a second concept.
_HIGH_PRIORITY_RECHECK_HOURS = 6.0
_MEDIUM_PRIORITY_RECHECK_HOURS = 24.0
_LOW_PRIORITY_RECHECK_HOURS = 72.0

# Backoff: consecutive SOURCE_FETCH_FAILED runs push the next allowed check
# further out (min(interval * 2^consecutive_failures, cap)) so a genuinely
# unreachable source isn't hammered every scan.
_BACKOFF_CAP_HOURS = 24.0 * 14


def _recheck_interval_hours(priority_score: float) -> float:
    if priority_score >= 40.0:
        return _HIGH_PRIORITY_RECHECK_HOURS
    if priority_score >= 15.0:
        return _MEDIUM_PRIORITY_RECHECK_HOURS
    return _LOW_PRIORITY_RECHECK_HOURS


def _last_run_info(storage: Storage, provider_market_id: str) -> tuple[datetime | None, int]:
    """Real last-run timestamp + consecutive-failure streak for one market,
    derived from the persisted research_runs history — no separate
    scheduler-state table needed."""
    rows = storage.get_research_runs(provider_market_id=provider_market_id, limit=10)
    if not rows:
        return None, 0
    last_run_at = None
    try:
        last_run_at = datetime.fromisoformat(rows[0]["run_at"])
    except (KeyError, ValueError, TypeError):
        pass
    consecutive_failures = 0
    for row in rows:
        detail = row.get("detail_json")
        status = None
        if detail:
            import json as _json

            try:
                status = _json.loads(detail).get("source_fetch_status")
            except (ValueError, TypeError):
                status = None
        if status == "SOURCE_FETCH_FAILED":
            consecutive_failures += 1
        else:
            break
    return last_run_at, consecutive_failures


def build_queue_from_db(storage: Storage):
    """Real, shared queue-building logic: reads all unresolved markets from
    the DB, builds real MarketSignal objects (real source-coverage flag
    from news_market_links), ranks them with the existing
    research_queue.build_research_queue(). Used by run_recurring_research(),
    cli.cmd_research_run(), and the read-only /research-queue API endpoint
    -- one real implementation, not three."""
    from .research_queue import MarketSignal, build_research_queue

    unresolved = storage.connection.execute(
        "SELECT market_id, provider, provider_market_id, question, category, "
        "classified_category, resolution_source FROM markets "
        "WHERE resolution_status IS NULL OR resolution_status != 'resolved'"
    ).fetchall()

    signals = []
    rows_by_id: dict[str, dict] = {}
    for market_id, provider, provider_market_id, question, category, classified_category, resolution_source in unresolved:
        row = {
            "market_id": market_id, "provider": provider, "provider_market_id": provider_market_id,
            "question": question, "category": category, "classified_category": classified_category,
            "resolution_source": resolution_source,
        }
        rows_by_id[market_id] = row
        link_count = storage.connection.execute(
            "SELECT COUNT(*) FROM news_market_links WHERE provider = ? AND provider_market_id = ?",
            (provider, provider_market_id),
        ).fetchone()[0]
        signals.append(MarketSignal(
            market_id=market_id, question=question or "", category=classified_category or category,
            event_type=None, market_probability=None, model_hypothesis_probability=None,
            time_remaining_hours=None, critical_gap_count=0, high_gap_count=0,
            has_source_coverage=link_count > 0,
        ))
    return rows_by_id, build_research_queue(signals)


def run_recurring_research(
    storage: Storage, settings, limit: int = 10, max_cost_usd: float = 1.0,
) -> list[ResearchRunObservability]:
    """Real Recurring Ingestion: ranks all unresolved markets with the
    existing Research Queue, then runs the top-N through the same
    run_research_for_market() executor — but SKIPS any market whose real
    last research_runs row is still within its priority-tiered recheck
    interval (with real exponential backoff on consecutive source-fetch
    failures), so unchanged sources are never reprocessed every scan.
    Called from cli.cmd_scan(--research) -- the EXISTING scan loop, no
    second parallel scheduler."""
    rows_by_id, queue = build_queue_from_db(storage)
    now = datetime.now(UTC)
    spent = 0.0
    records: list[ResearchRunObservability] = []
    for entry in queue:
        if len(records) >= limit or spent >= max_cost_usd:
            break
        last_run_at, consecutive_failures = _last_run_info(storage, entry.market_id)
        if last_run_at is not None:
            interval = _recheck_interval_hours(entry.priority_score)
            if consecutive_failures > 0:
                interval = min(interval * (2 ** consecutive_failures), _BACKOFF_CAP_HOURS)
            elapsed_hours = (now - last_run_at).total_seconds() / 3600.0
            if elapsed_hours < interval:
                continue  # too soon per this market's real priority tier / backoff
        record = run_research_for_market(
            storage, settings, rows_by_id[entry.market_id], trigger="recurring_scan",
        )
        spent += record.cost_usd
        records.append(record)
    return records
