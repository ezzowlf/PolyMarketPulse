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

import json
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
    # Phase 7.14: the real product-facing outcome of this research run --
    # did closing (part of) the data gap actually change what the user
    # sees? Computed via the same product_mode_for_prediction() the API
    # uses, never a second classification. "unchanged" is the honest,
    # common outcome (most single research runs find nothing new).
    product_mode_before: str | None = None
    product_mode_after: str | None = None
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


def _fetch_and_persist_legislation_claim(
    storage: Storage, question: str, provider: str, provider_market_id: str,
) -> dict:
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

    # Phase D: a genuinely new GovTrack status for this exact bill
    # supersedes every prior GovTrack claim already linked to this market
    # -- a real, identified successor, not an age-based guess.
    if newly_inserted:
        prior_links = storage.get_claim_market_links(provider, provider_market_id)
        for link in prior_links:
            if link["source_id"] == "govtrack" and link["claim_id"] != claim.claim_id:
                storage.mark_claim_superseded(link["claim_id"], claim.claim_id)

    # Real claim-type classification (not a fuzzy guess): a final
    # presidential action (signed/vetoed) directly resolves most
    # legislation markets; any other real GovTrack status update only
    # confirms one step of the resolution path.
    claim_type = (
        "DIRECT_RESOLUTION"
        if status.current_status in ("enacted_signed", "enacted_veto_override", "vetoed", "prov_kill_veto")
        else "PATH_STEP" if step_name else "CONTEXT"
    )
    storage.save_claim_market_link(claim.claim_id, provider, provider_market_id, claim_type)

    # Phase C: real Event/Entity/Relation graph write path -- reuses the
    # existing migration-12 schema, no parallel structure. A GovTrack
    # status update IS a real, dated, officially-sourced event; it
    # SIGNALS (not CAUSES -- this is a status confirmation, not a causal
    # mechanism) this market's resolution direction at a KNOWN evidence
    # tier (official government record).
    bill_title = status.title or f"H.R.{number}"
    event_id = storage.save_event(
        title=f"GovTrack status: {bill_title} -> {status.current_status}",
        event_type="legislative_progress",
        occurred_at=claim.timestamp.isoformat() if claim.timestamp else None,
        geographic_scope="country", source="govtrack", source_url=status.link,
    )
    entity_id = storage.save_entity(bill_title, entity_type="legislation", geographic_scope="country")
    if event_id is not None and entity_id is not None:
        storage.save_event_entity_link(event_id, entity_id, role="subject")
    storage.save_event_relation(
        source_event_id=event_id, source_entity_id=entity_id, target_entity_id=entity_id,
        target_provider=provider, target_provider_market_id=provider_market_id,
        relation_type="SIGNALS", direction=claim.direction or "neutral",
        evidence_tier="KNOWN", confidence=claim.confidence, source_quality="primary_official",
        valid_from=claim.timestamp.isoformat() if claim.timestamp else None,
        detail=predicate,
    )
    return {
        "attempted": True, "fetch_status": "OK", "bill_number": number,
        "current_status": status.current_status, "resolution_step": step_name,
        "claim_type": claim_type,
        "claim_newly_inserted": newly_inserted, "source_url": status.link,
    }


# Real question-text -> PortWatch chokepoint name mapping. Deliberately
# narrow/explicit (not fuzzy) so this only ever fires for markets that are
# genuinely about one of these two specific chokepoints.
_CHOKEPOINT_KEYWORDS = {
    "hormuz": "Strait of Hormuz",
    "bab-el-mandeb": "Bab-el-Mandeb",
    "bab el-mandeb": "Bab-el-Mandeb",
}

_TRANSIT_THRESHOLD_RE = re.compile(r"(?:equal to or above|at or above|>=|above)\s+(\d+)", re.IGNORECASE)


def _fetch_and_persist_chokepoint_claim(
    storage: Storage, question: str, resolution_text: str | None,
    provider: str, provider_market_id: str,
) -> dict:
    """Real, targeted second-source fetch for strategic-waterway markets:
    identifies the specific chokepoint from the question text, fetches its
    REAL daily transit-call data from IMF PortWatch's own public dataset
    (providers/imf_portwatch.py -- the exact resolution-question data
    source these markets cite, not a generic news article), and persists
    it as a real, resolution-relevant, PRIMARY_CONFIRMED claim with a real
    direction (derived from comparing the real 7-day average to the real
    threshold parsed from the resolution text, when present).

    This is the deliberate answer to "find a real second independent
    source, not just any Hormuz-adjacent article" -- IMF PortWatch is an
    official international-organization data provider, genuinely
    independent of news reporting, and its data directly and quantitatively
    answers the resolution question these specific markets ask.

    Never raises; a fetch/parse failure or a question that doesn't
    reference a known chokepoint just means no claim this run."""
    lowered = (question or "").lower()
    chokepoint = next((name for kw, name in _CHOKEPOINT_KEYWORDS.items() if kw in lowered), None)
    if chokepoint is None:
        return {"attempted": False}

    from .providers.imf_portwatch import fetch_chokepoint_transit_data

    data = fetch_chokepoint_transit_data(chokepoint)
    if data is None:
        return {"attempted": True, "fetch_status": "SOURCE_FETCH_FAILED", "chokepoint": chokepoint}

    threshold_match = _TRANSIT_THRESHOLD_RE.search(resolution_text or "")
    threshold = int(threshold_match.group(1)) if threshold_match else None
    avg = data.seven_day_average
    resolution_status = "OK"
    if threshold is not None and avg is not None:
        # Real, direct comparison against the market's own real threshold —
        # a genuine resolution-relevant signal, not an inference.
        direction = "positive" if avg >= threshold else "negative"
        predicate = f"7-day average transit calls = {avg} (threshold {threshold})"
    elif avg is not None:
        # Real transit data exists, but no usable threshold could be parsed
        # from the resolution text (either it's genuinely absent, or its
        # phrasing doesn't match the known patterns). This must be
        # surfaced honestly as ambiguous -- never silently treated as "no
        # opinion" (neutral) as if everything were fine, since a real
        # data point exists that we simply can't interpret against the
        # rule yet.
        direction = "neutral"
        resolution_status = "RESOLUTION_AMBIGUOUS"
        predicate = (
            f"7-day average transit calls = {avg}; no parseable resolution threshold "
            f"found in resolution text (RESOLUTION_AMBIGUOUS)"
        )
    else:
        direction = "neutral"
        predicate = "no data"

    import hashlib as _hashlib

    from .claims import Claim

    latest_date = data.observations[-1][0] if data.observations else None
    claim_id = _hashlib.sha256(
        f"imf_portwatch:{chokepoint}:{latest_date}:{avg}".encode()
    ).hexdigest()[:32]
    claim = Claim(
        claim_id=claim_id, subject=f"{chokepoint} transit calls", predicate=predicate, object=None,
        speaker=None, source_id="imf_portwatch", source_url="https://portwatch.imf.org",
        timestamp=datetime.combine(latest_date, datetime.min.time(), tzinfo=UTC) if latest_date else None,
        verification_status="PRIMARY_CONFIRMED", confidence=0.95,
        entities=(chokepoint,), location=chokepoint,
        raw_reference=f"observations={len(data.observations)}",
        event_type="waterway_status", direction=direction, resolution_step=None,
    )
    newly_inserted = storage.save_claim(claim)
    storage.save_claim_source(
        claim.claim_id, "imf_portwatch", "https://portwatch.imf.org",
        claim.timestamp.isoformat() if claim.timestamp else None,
    )

    # A direct real threshold comparison IS the resolution question itself
    # for these markets (real, quantitative, directly resolution-relevant);
    # without a usable threshold it's still a real quantitative data point
    # for the underlying state, just not directly dispositive.
    claim_type = "DIRECT_RESOLUTION" if direction != "neutral" else "QUANTITATIVE_SIGNAL"
    storage.save_claim_market_link(claim.claim_id, provider, provider_market_id, claim_type)

    # Phase C: real Event/Entity/Relation graph write path (mirrors the
    # GovTrack wiring above). IMF PortWatch transit data is a real,
    # quantitative, officially-sourced observation of the chokepoint's
    # state -- KNOWN tier when it directly resolves against the market's
    # own threshold, SUPPORTED (still quantitative-eligible) when the
    # data is real but no threshold could be matched against it.
    event_id = storage.save_event(
        title=f"IMF PortWatch transit data: {chokepoint} ({latest_date})",
        event_type="waterway_status",
        occurred_at=claim.timestamp.isoformat() if claim.timestamp else None,
        geographic_scope="region", source="imf_portwatch", source_url="https://portwatch.imf.org",
    )
    entity_id = storage.save_entity(chokepoint, entity_type="region", geographic_scope="global")
    if event_id is not None and entity_id is not None:
        storage.save_event_entity_link(event_id, entity_id, role="subject")
    storage.save_event_relation(
        source_event_id=event_id, source_entity_id=entity_id, target_entity_id=entity_id,
        target_provider=provider, target_provider_market_id=provider_market_id,
        relation_type="SIGNALS", direction=direction,
        evidence_tier="KNOWN" if direction != "neutral" else "SUPPORTED",
        strength=1.0 if direction != "neutral" else None,
        confidence=claim.confidence, source_quality="primary_official",
        valid_from=claim.timestamp.isoformat() if claim.timestamp else None,
        detail=predicate,
    )
    return {
        "attempted": True, "fetch_status": "OK", "chokepoint": chokepoint,
        "seven_day_average": avg, "threshold": threshold, "direction": direction,
        "resolution_status": resolution_status, "claim_type": claim_type,
        "claim_newly_inserted": newly_inserted,
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
    from .product_mode import product_mode_for_prediction

    product_mode_before = product_mode_for_prediction(pred_before)["product_mode"]

    # --- Real, targeted official-source fetch for legislation-shaped
    # markets (e.g. "H.R.3633") — GovTrack, not another GDELT query.
    legislation_result = _fetch_and_persist_legislation_claim(
        storage, question, provider, provider_market_id
    )
    # Real root-cause fix: this project's `resolution_source` DB column is
    # often empty even when the market's real resolution rule text IS
    # present (under `description`, populated by the provider fetch) —
    # confirmed live for Hormuz (2774056): resolution_source=None,
    # description=the full real IMF PortWatch resolution rule text. Try
    # resolution_source first (more specific when present), fall back to
    # description rather than leaving a real, available resolution rule
    # unused.
    resolution_text = market_row.get("resolution_source") or market_row.get("description")
    chokepoint_result = _fetch_and_persist_chokepoint_claim(
        storage, question, resolution_text, provider, provider_market_id
    )

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
    product_mode_after = product_mode_for_prediction(pred_after)["product_mode"]

    claims_after_total = _claims_total(storage)
    links_after = _news_links_total(storage, provider, provider_market_id)
    groups_after, primary_after = _independent_groups_and_primary(pred_after)

    claims_extracted_this_run = max(0, claims_after_total - claims_before_total)

    # Phase 7.6: persistent gap-closure tracking. A gap is only ever
    # CLOSED when the targeted fetch found genuinely NEW information
    # (claim_newly_inserted) -- a successful-but-empty re-check (Clarity/
    # Hormuz's real idempotent case) stays OPEN, never a fake closure.
    _now_iso = datetime.now(UTC).isoformat()
    gap_records: list[dict] = []
    if legislation_result.get("attempted"):
        status = legislation_result.get("fetch_status")
        result_status = (
            "BLOCKED_PROVIDER" if status == "SOURCE_FETCH_FAILED"
            else "CLOSED" if legislation_result.get("claim_newly_inserted") else "OPEN"
        )
        gap_records.append({
            "gap_type": "MISSING_RESOLUTION_DATA", "provider_attempted": "govtrack",
            "target_information": "official bill status / resolution step",
            "source_reference": legislation_result.get("source_url"),
            "result_status": result_status, "failure_reason": status if status != "OK" else None,
        })
    if chokepoint_result.get("attempted"):
        status = chokepoint_result.get("fetch_status")
        result_status = (
            "BLOCKED_PROVIDER" if status == "SOURCE_FETCH_FAILED"
            else "CLOSED" if chokepoint_result.get("claim_newly_inserted") else "OPEN"
        )
        gap_records.append({
            "gap_type": "MISSING_STRUCTURED_DATA", "provider_attempted": "imf_portwatch",
            "target_information": "structured chokepoint transit data",
            "result_status": result_status, "failure_reason": status if status != "OK" else None,
        })
    if not gap_records:
        reason = pred_after.numeric_model_reason_code
        if reason == "NO_ARCHETYPE":
            gap_records.append({
                "gap_type": "NO_ARCHETYPE", "provider_attempted": None,
                "target_information": "no supported forecast archetype for this market",
                "result_status": "NOT_APPLICABLE", "failure_reason": None,
            })
        else:
            result_status = (
                "BLOCKED_PROVIDER" if source_fetch_status == "SOURCE_FETCH_FAILED"
                else "CLOSED" if claims_extracted_this_run > 0 else "OPEN"
            )
            gap_records.append({
                "gap_type": "MISSING_PRIMARY_SOURCE", "provider_attempted": "gdelt",
                "target_information": "discovery-sourced primary/secondary news coverage",
                "result_status": result_status,
                "failure_reason": source_fetch_status if source_fetch_status != "OK" else None,
            })
    for gap in gap_records:
        storage.save_gap_closure({
            "market_id": market_id, "provider": provider, "provider_market_id": provider_market_id,
            "gap_type": gap["gap_type"], "gap_key": f"{provider}:{provider_market_id}:{gap['gap_type']}",
            "target_information": gap["target_information"], "criticality": "HIGH",
            "provider_attempted": gap["provider_attempted"], "source_reference": gap.get("source_reference"),
            "research_started_at": _now_iso, "research_finished_at": datetime.now(UTC).isoformat(),
            "result_status": gap["result_status"], "failure_reason": gap["failure_reason"],
            "previous_gap_state": product_mode_before, "new_gap_state": product_mode_after,
            "product_mode_before": product_mode_before, "product_mode_after": product_mode_after,
            "model_probability_before": pred_before.model_hypothesis_probability,
            "model_probability_after": pred_after.model_hypothesis_probability,
            "closed_at": _now_iso if gap["result_status"] == "CLOSED" else None,
            "next_retry": None,
        })

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
        product_mode_before=product_mode_before,
        product_mode_after=product_mode_after,
        final_status=pred_after.forecast_status or "",
        duration_ms=duration_ms,
        cost_usd=0.0,  # GDELT/news pipeline is free/keyless; no paid calls made
        detail={
            "gdelt_query": query,
            "source_fetch_status": source_fetch_status,
            # Routing is explicit and audit-visible.  A targeted primary
            # route is only listed when it was actually applicable/attempted;
            # GDELT remains discovery, never a fake substitute for it.
            "source_attempts": [
                *([{"provider": "govtrack", "role": "primary", "status": legislation_result.get("fetch_status", "NOT_APPLICABLE"), "reason": "legislation resolution status"}] if legislation_result.get("attempted") else []),
                *([{"provider": "imf_portwatch", "role": "primary", "status": chokepoint_result.get("fetch_status", "NOT_APPLICABLE"), "reason": "resolution-relevant chokepoint transit data"}] if chokepoint_result.get("attempted") else []),
                {"provider": "gdelt", "role": "discovery", "status": source_fetch_status, "reason": "market-specific discovery query"},
            ],
            "alternative_providers": [],
            "legislation": legislation_result,
            "chokepoint": chokepoint_result,
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
    """Real last-run timestamp + consecutive-non-productive-run streak for
    one market, derived from the persisted research_runs history — no
    separate scheduler-state table needed.

    Phase 7.8.10 loop guard: a run counts against the backoff streak when it
    was either a genuine transport failure (SOURCE_FETCH_FAILED) OR a real
    fetch that extracted zero new claims (claims_extracted == 0) -- the
    latter is the "endless NO_NEW_INFORMATION loop" case explicitly called
    out in the spec: a source that keeps answering successfully but never
    yields anything new must still back off over time, not be re-fetched at
    the same short interval forever. A single row with claims_extracted > 0
    (real new information) resets the streak immediately."""
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
        claims_extracted = row.get("claims_extracted") or 0
        if status == "SOURCE_FETCH_FAILED" or claims_extracted == 0:
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

    # Use the latest persisted forecast state rather than silently filling
    # the real ranking inputs with None/zero. This keeps the queue read-only
    # (the /research-queue endpoint must not compute/persist forecasts) while
    # allowing actual divergence, deadline and data-gap signals to select a
    # current golden case or recurring-research candidate.
    unresolved = storage.connection.execute(
        """
        SELECT m.market_id, m.provider, m.provider_market_id, m.question, m.category,
               m.classified_category, m.resolution_source, m.description, m.end_date,
               p.market_yes_probability, p.model_hypothesis_probability,
               p.data_gap_summary_json,
               EXISTS(SELECT 1 FROM news_market_links n
                      WHERE n.provider = m.provider AND n.provider_market_id = m.provider_market_id),
               (SELECT COUNT(*) FROM social_signal_markets sm JOIN social_signals ss ON ss.signal_id=sm.signal_id
                WHERE sm.market_id=m.market_id AND ss.signal_status IN ('RUMOR','EARLY_SIGNAL','PARTIALLY_CONFIRMED')),
               (SELECT COUNT(*) FROM coherence_audits ca JOIN market_relationships mr ON mr.id=ca.relationship_id
                WHERE ca.id IN (SELECT id FROM coherence_audits x WHERE x.relationship_id=ca.relationship_id ORDER BY audited_at DESC LIMIT 1)
                AND ca.status='COHERENCE_WARNING' AND (mr.market_id_a=m.market_id OR mr.market_id_b=m.market_id))
        FROM markets m
        LEFT JOIN prediction_snapshots p ON p.id = (
            SELECT ps.id FROM prediction_snapshots ps
            WHERE ps.market_id = m.market_id ORDER BY ps.created_at DESC, ps.id DESC LIMIT 1
        )
        WHERE m.resolution_status IS NULL OR m.resolution_status != 'resolved'
        """
    ).fetchall()

    signals = []
    rows_by_id: dict[str, dict] = {}
    now = datetime.now(UTC)
    for (
        market_id, provider, provider_market_id, question, category, classified_category,
        resolution_source, description, end_date, market_probability, model_probability,
        gap_summary_json, has_news_links, early_signal_count, coherence_warning_count,
    ) in unresolved:
        row = {
            "market_id": market_id, "provider": provider, "provider_market_id": provider_market_id,
            "question": question, "category": category, "classified_category": classified_category,
            "resolution_source": resolution_source, "description": description,
        }
        rows_by_id[market_id] = row
        time_remaining_hours = None
        if end_date:
            try:
                parsed = datetime.fromisoformat(end_date)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                time_remaining_hours = (parsed - now).total_seconds() / 3600.0
            except (TypeError, ValueError):
                pass
        gap_summary = {}
        if gap_summary_json:
            try:
                gap_summary = json.loads(gap_summary_json)
            except (TypeError, ValueError):
                pass
        signals.append(MarketSignal(
            market_id=market_id, question=question or "", category=classified_category or category,
            event_type=None, market_probability=market_probability, model_hypothesis_probability=model_probability,
            time_remaining_hours=time_remaining_hours,
            critical_gap_count=int(gap_summary.get("critical", 0) or 0),
            high_gap_count=int(gap_summary.get("high", 0) or 0),
            has_source_coverage=bool(has_news_links),
            early_signal_count=early_signal_count,
            coherence_warning_count=coherence_warning_count,
        ))
    return rows_by_id, build_research_queue(signals)


def enrich_queue_with_gap_voi(storage: Storage, rows_by_id: dict, queue: list, limit: int = 20) -> list[dict]:
    """Phase 7.8.8: attach real, gap-level Next-Best-Research-Action detail
    (data_coverage.py's compute_data_coverage/derive_next_research_action)
    to the existing market-priority Research Queue, WITHOUT building a
    second queue -- the existing research_queue.build_research_queue()
    ranking (divergence/deadline/data-gap/source-coverage signals) is still
    the base ranking; this only adds gap-level VOI on top and re-sorts.

    Deliberately read-only and bounded: only the top `limit` already-
    priority-ranked entries get a real compute_prediction() call (this is
    the same cost /prediction already pays per market, just for a few
    markets instead of one) -- this function must never be used inside the
    real execution loop (run_recurring_research already computes its own
    prediction per market via run_research_for_market -> get_prediction,
    so calling this here would just double the real work for no benefit).

    Ranking: primarily by gap-level voi_score (the more specific, more
    actionable signal), with the pre-existing market-priority_score as an
    explicit real tie-breaker -- neither signal is silently discarded."""
    from .prediction.data_coverage import compute_data_coverage, derive_next_research_action
    from .product_mode import product_mode_for_prediction

    enriched: list[dict] = []
    for entry in queue[:limit]:
        row = rows_by_id.get(entry.market_id, {})
        base = {
            "market_id": entry.market_id,
            "question": entry.question,
            "priority_score": entry.priority_score,
            "reasons": list(entry.reasons),
            "category": row.get("classified_category") or row.get("category"),
        }
        try:
            prediction = get_prediction(storage, entry.market_id)
            coverage = compute_data_coverage(prediction)
            action = derive_next_research_action(prediction, coverage, storage=storage)
            product_mode = product_mode_for_prediction(prediction).get("product_mode")
        except Exception:  # noqa: BLE001 - a display enrichment must never break the queue itself
            action = None
            product_mode = None
        base.update({
            "product_mode": product_mode,
            "gap_key": action.get("gap_key") if action else None,
            "target_information": action.get("target_information") if action else None,
            "preferred_provider": action.get("preferred_provider") if action else None,
            "fallback_provider": action.get("fallback_provider") if action else None,
            "provider_health": action.get("provider_health") if action else None,
            "closability": action.get("closability") if action else None,
            "expected_product_effect": action.get("expected_product_effect") if action else None,
            "voi_score": action.get("voi_score", 0) if action else 0,
            "action_type": action.get("action_type") if action else None,
            "human_summary": action.get("human_summary") if action else None,
            "reason": action.get("reason") if action else None,
        })
        enriched.append(base)

    enriched.sort(key=lambda e: (e["voi_score"], e["priority_score"]), reverse=True)
    return enriched


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
