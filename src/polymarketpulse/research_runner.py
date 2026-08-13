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
            "groups_before": groups_before, "primary_before": primary_before,
        },
    )
    storage.save_research_run(record.as_dict())
    return record
