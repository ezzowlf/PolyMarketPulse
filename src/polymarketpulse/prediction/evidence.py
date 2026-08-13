"""Independent Evidence & Early-Signal Engine.

Computes a probability estimate for a market that is derived **only** from
linked public news/OSINT-style evidence (`news_events` / `news_market_links`,
populated by news/rss.py + news/gdelt.py — free, lawfully public sources
only, never stolen/hacked/insider/private data) — the market's own current
price is never used as an input or anchor. Only *after* this independent
number is computed is it compared against the market price, to produce a
divergence and an information-edge score.

This is the module the rest of the engine (and the dashboard) must consult
whenever it wants to know "is there real, independent evidence here, or are
we just echoing what the market already thinks?" — if there isn't enough
evidence, `available` is False and no number is invented; the honest answer
is "keine unabhängige Schätzung möglich", never a silent fallback to the
market price.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..claims import ExtractedClaim

from ..claims import group_claims_by_normalization
from ..source_registry import (
    _resolve_cluster_key,
    calculate_source_quality_score,
    get_source_definition,
)
from .base_rates import EXTRAORDINARY_EVENT_TYPES, get_base_rate
from .bayesian import bayesian_update
from .news import _trust_for_source, score_sentiment
from .resolution_rules import parse_resolution_conditions
from .semantics import classify_evidence_relation, extract_event, parse_market_proposition

# Extraordinary-event guard (Phase B3): below this many DIRECT_YES/DIRECT_NO
# tier evidence items, an extraordinary event_type's independent estimate is
# dampened back toward its base rate (or, if no base rate is defined for the
# type, toward the same neutral 0.5 prior the Bayesian update itself already
# starts from — see below) rather than being allowed to swing freely off a
# single weak/ambiguous headline.
EXTRAORDINARY_DIRECT_EVIDENCE_REQUIRED = 2
# Maximum allowed distance from the anchor (base rate, or 0.5 if none) when
# the guard fires, keyed by how many direct-tier items were actually found.
_EXTRAORDINARY_MAX_SWING_BY_DIRECT_COUNT = {0: 0.03, 1: 0.08}

# Evidence loses relevance faster than the general news submodel's 48h
# half-life — "early signal" freshness is the whole point of this module.
# This is the DEFAULT curve, used for anything not explicitly categorized
# below — it stays exactly as it was before the category-aware fix.
RECENCY_HALF_LIFE_HOURS = 24.0
BREAKING_WINDOW_HOURS = 48.0

# HANDOFF Part-4 fix: a single global 24h half-life conflates two different
# things this codebase's evidence really contains: (1) fast-moving
# situational/directional signal (a ceasefire rumor, a troop-movement
# report) that genuinely goes stale within hours-to-days, and (2) discrete,
# durable STATE CHANGES (a bill passing the House, a central bank's rate
# decision) that remain just as true — and just as relevant to a market's
# resolution — weeks or months after they happened. Root cause, proven
# live: a real, correctly-classified DIRECT_YES claim for H.R.3633's real
# 2025-07-17 House-passage vote decayed to recency_weight == 0.0 under the
# flat 24h curve purely because of its age, even though the fact itself is
# still 100% true and load-bearing for the market's resolution today.
#
# This lookup is keyed by `MarketProposition.event_type` (the same field
# `semantics.py`/`world_state.py` already use to distinguish these
# categories) and supplies a half-life in HOURS. Anything not listed here
# falls back to the unchanged default `RECENCY_HALF_LIFE_HOURS` curve — this
# is an additive, narrowly-scoped correction, not a global loosening.
#
# Design reasoning per category (real, documented, not tuned to force a
# specific number):
#   - "legislation": a recorded vote/committee action/signature is a
#     discrete procedural fact, not a rumor — once true it stays true until
#     the NEXT recorded step supersedes it. 30-day half-life (720h): still
#     >80% weight at 1 week, ~50% at a month, decaying further only because
#     evidence this old MIGHT have been superseded by an unfetched later
#     step, not because the fact itself decays.
#   - "war_escalation" / "ceasefire": explicitly the opposite direction —
#     situational, rapidly-evolving ground truth (a ceasefire can collapse
#     or a front can shift within hours). Kept AT OR BELOW the default,
#     never loosened: 12h half-life, strict on purpose per this project's
#     existing real-time-sensitive design intent for geopolitics.
#   - "rate_cut" / "rate_hike" / "rate_hold": a Fed decision is "the
#     current rate" until the next scheduled FOMC meeting (~6-8 week
#     cadence), not until an arbitrary hour count — 720h (30 days) half-life
#     mirrors that natural release cadence rather than treating a rate
#     decision like breaking news that stales out in a day.
EVENT_TYPE_RECENCY_HALF_LIFE_HOURS: dict[str, float] = {
    "legislation": 24.0 * 30,  # 720h — durable procedural/state-change fact
    "war_escalation": 12.0,  # faster than default — situational, strict
    "ceasefire": 12.0,  # faster than default — situational, strict
    "rate_cut": 24.0 * 30,  # 720h — matches FOMC's real meeting cadence
    "rate_hike": 24.0 * 30,
    "rate_hold": 24.0 * 30,
}
MIN_EVIDENCE_ITEMS_FOR_ESTIMATE = 2  # a single headline is not "independent evidence"

# Below this term-overlap link confidence, a headline that merely mentions
# the market's subject entity in a positive/negative tone is NOT treated as
# directional evidence for the resolution condition. This is the fix for a
# real, observed failure mode: an unrelated "Trump delivers wins in Nevada"
# headline (loosely linked via the shared word "Trump") was being scored as
# YES-evidence for a "Trump out as President" market purely because of its
# positive tone — sentiment about the subject is not the same thing as
# evidence about the specific proposition the market resolves on. Explicit
# yes/no resolution-condition term matches (see resolution_rules.py) are
# exempt from this gate — those are on-topic by construction, not by
# incidental keyword overlap.
SENTIMENT_FALLBACK_MIN_RELEVANCE = 0.35


@dataclass(frozen=True)
class EvidenceFactor:
    news_event_id: int
    title: str
    source: str
    source_domain: str
    url: str
    published_at: str | None
    reliability: float  # 0..1
    tone: float  # -1..+1, normalized (from GDELT tone/100 or lexicon sentiment) — AUXILIARY signal only,
    # see semantics.classify_evidence_relation: tone alone never determines matched_condition above WEAK tier.
    matched_condition: str | None  # "yes" | "no" | None
    recency_weight: float  # 0..1
    link_confidence: float  # 0..1, topical match confidence from news/linker.py
    relation_label: str = "AMBIGUOUS"  # semantics.EvidenceRelationLabel — the real entailment classification
    entailment: str = "NEUTRAL"  # "ENTAILS" | "CONTRADICTS" | "NEUTRAL"
    relation_weight: float = 0.0  # semantics.EvidenceRelation.quantitative_weight, 0..1
    source_type: str = "OTHER"  # SourceType aus source_registry.py
    independence_group: str | None = None  # Für Cluster-Erkennung

    def as_dict(self) -> dict:
        return {
            "title": self.title, "source": self.source, "source_domain": self.source_domain,
            "url": self.url, "published_at": self.published_at, "reliability": self.reliability,
            "tone": self.tone, "matched_condition": self.matched_condition,
            "recency_weight": self.recency_weight, "relation_label": self.relation_label,
            "entailment": self.entailment, "relation_weight": self.relation_weight,
            "link_confidence": self.link_confidence,
            "source_type": self.source_type,
            "independence_group": self.independence_group,
        }


@dataclass(frozen=True)
class IndependentEvidenceResult:
    available: bool
    independent_yes_probability: float | None
    confirmation_count: int
    source_quality_score: float | None  # 0..100
    time_since_first_report_hours: float | None
    contradiction_detected: bool
    breaking: bool
    information_edge_score: float | None  # 0..100, separate from net_yes_edge
    divergence: float | None  # independent_yes_probability - market_yes_probability
    evidence_for_yes: tuple[EvidenceFactor, ...] = field(default_factory=tuple)
    evidence_for_no: tuple[EvidenceFactor, ...] = field(default_factory=tuple)
    not_yet_priced_in: tuple[EvidenceFactor, ...] = field(default_factory=tuple)
    # I2 (additive): evidence items that WERE seen (linked, scored) but
    # classified CONTEXT/IRRELEVANT/AMBIGUOUS (matched_condition is None,
    # relation_weight 0) — never blended into the estimate. Kept visible,
    # separately, rather than silently dropped, so the UI can show a
    # "verworfen / nicht relevant" section instead of pretending these
    # articles were never considered.
    discarded_evidence: tuple[EvidenceFactor, ...] = field(default_factory=tuple)
    detail: str = ""
    # Phase B3: extraordinary-event guard visibility — whether the dampening
    # step fired for this market, and why, so the UI/audit trail never has
    # to guess why a number looks smaller than the raw evidence math implies.
    extraordinary_guard_applied: bool = False
    extraordinary_guard_detail: str | None = None
    # Counter-evidence (additive, diagnostic-only): count of REAL claim-vs-
    # claim contradictions detected among claims extracted from this
    # market's linked evidence (claims.detect_claim_contradictions) and
    # persisted into `claim_counter_evidence`. `claim_status_counts` is a
    # verification_status -> count breakdown of the claim groups formed for
    # this market (SINGLE_SOURCE/MULTI_SOURCE/PRIMARY_CONFIRMED/DISPUTED).
    # Zero counter_evidence_count is the honest, common case (absence of
    # contradiction) — this field must never be read as a positive signal,
    # only its presence (> 0) is meaningful.
    counter_evidence_count: int = 0
    claim_status_counts: dict = field(default_factory=dict)
    # Real PATH_STEP structured claims (via claim_market_links) for this
    # market -- deliberately NOT folded into evidence_for_yes/no (the
    # double-counting guard: a PATH_STEP claim's job is to update the
    # resolution path, world_state.py's _derive_resolution_path, never the
    # yes/no probability). Populated regardless of `available`, since a
    # market can have real path progress even while lacking enough
    # article-based evidence for a probability estimate (e.g. Clarity Act).
    # Each item: {"resolution_step": str, "source": str, "timestamp": str|None,
    # "detail": str}.
    path_step_claims: tuple[dict, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "independent_yes_probability": self.independent_yes_probability,
            "confirmation_count": self.confirmation_count,
            "source_quality_score": self.source_quality_score,
            "time_since_first_report_hours": self.time_since_first_report_hours,
            "contradiction_detected": self.contradiction_detected,
            "breaking": self.breaking,
            "information_edge_score": self.information_edge_score,
            "divergence": self.divergence,
            "evidence_for_yes": [e.as_dict() for e in self.evidence_for_yes],
            "evidence_for_no": [e.as_dict() for e in self.evidence_for_no],
            "not_yet_priced_in": [e.as_dict() for e in self.not_yet_priced_in],
            "discarded_evidence": [e.as_dict() for e in self.discarded_evidence],
            "detail": self.detail,
            "extraordinary_guard_applied": self.extraordinary_guard_applied,
            "extraordinary_guard_detail": self.extraordinary_guard_detail,
            "counter_evidence_count": self.counter_evidence_count,
            "claim_status_counts": dict(self.claim_status_counts),
            "path_step_claims": list(self.path_step_claims),
        }


def _unavailable(
    detail: str, factors: tuple = (), path_step_claims: tuple = (),
) -> IndependentEvidenceResult:
    """`factors`, when passed, are real already-extracted EvidenceFactor
    items (e.g. the one real linked article for a Hormuz-shaped market) —
    kept visible to the UI as evidence_for_yes/no/discarded even though the
    probability itself correctly stays unavailable, per the explicit
    requirement that "1 relevante Quelle vorhanden, unabhängige Bestätigung
    fehlt" be a real, inspectable statement rather than a generic "zu wenig
    Daten" with nothing to show for it."""
    evidence_for_yes = tuple(f for f in factors if f.matched_condition == "yes")
    evidence_for_no = tuple(f for f in factors if f.matched_condition == "no")
    discarded = tuple(f for f in factors if f.matched_condition is None)
    return IndependentEvidenceResult(
        available=False, independent_yes_probability=None, confirmation_count=0,
        source_quality_score=None, time_since_first_report_hours=None,
        contradiction_detected=False, breaking=False, information_edge_score=None,
        divergence=None, detail=detail,
        evidence_for_yes=evidence_for_yes, evidence_for_no=evidence_for_no,
        discarded_evidence=discarded, path_step_claims=path_step_claims,
    )


def _domain_reliability(source: str, source_domain: str) -> float:
    # Reuse the news submodel's curated trust table (keyed by source label,
    # e.g. "reuters"); fall back to a neutral 0.5 for unrecognized domains —
    # never 0 (would erase the source) or 1 (would over-trust it).
    for key in (source_domain.lower(), source.lower()):
        trust = _trust_for_source(key)
        if trust != 0.5:
            return trust
    return 0.5


def _persist_extracted_event(
    conn: sqlite3.Connection,
    provider: str,
    provider_market_id: str,
    title: str,
    event: object,
    news_event_id: int,
    published_at: str | None,
    news_source: str | None = None,
) -> None:
    """Phase H: additive-only persistence of the already-computed
    ExtractedEvent into the migration-12/15 `events` table, so it's usable
    by a future event graph instead of staying transient. Deliberately a
    pure side-effect with no return value consumed anywhere in the scoring
    math below — this must never change independent-evidence output.
    Wrapped so any failure (e.g. an older DB missing migration 15's
    columns) degrades to a no-op rather than breaking evidence scoring."""
    import json as _json

    try:
        conn.execute(
            """
            INSERT INTO events (
                title, event_type, occurred_at, geographic_scope, source, source_url, created_at,
                actors_json, action, target, expected_time, status, source_type, certainty,
                provider, provider_market_id, news_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title, event.event_type, event.event_time or published_at, event.location,
                event.source or news_source, None, datetime.now(UTC).isoformat(),
                _json.dumps(list(event.actors)), event.action, event.target, event.expected_time,
                event.status, event.source_type, event.certainty,
                provider, provider_market_id, news_event_id,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        # Additive persistence only — never let a storage-layer issue
        # (e.g. a not-yet-migrated DB) break evidence scoring itself.
        pass


def _persist_claims_for_event(
    conn: sqlite3.Connection,
    event: object,
    news_event_id: int,
    source: str | None,
    source_url: str | None,
    published_at: str | None,
) -> ExtractedClaim | None:
    """Additive-only: convert the already-computed ExtractedEvent into an
    ExtractedClaim (claims.py) and return it for later grouping/persistence.

    This never influences the probability math above — it is purely a
    side-channel into the `claims`/`claim_groups`/`claim_sources` tables so
    claims get stable IDs and future verification-status tracking, exactly
    like `_persist_extracted_event` does for the `events` table. Any
    failure (older DB missing the claims migration, extraction returning
    None, etc.) degrades to a no-op and must never break evidence scoring.
    """
    from polymarketpulse.claims import extract_claim_from_event

    try:
        timestamp = None
        if published_at:
            try:
                timestamp = datetime.fromisoformat(published_at)
            except ValueError:
                timestamp = None
        return extract_claim_from_event(
            event, source_id=source or "unknown", source_url=source_url, timestamp=timestamp
        )
    except (AttributeError, TypeError, ValueError):
        # Purely additive infrastructure — never let claim extraction
        # break the real evidence-scoring path above.
        return None


def _persist_claim_groups(conn: sqlite3.Connection, extracted_claims: list) -> dict:
    """Group and persist claims collected across all linked articles for
    one market (real deduplication when the same underlying claim shows up
    from multiple sources) — additive only, wrapped so a storage-layer
    issue never propagates into evidence scoring.

    Also detects REAL claim-vs-claim contradictions among the groups for
    this market (see claims.detect_claim_contradictions) and persists them
    into the `claim_counter_evidence` table (schema existed, migration
    015-era, but nothing previously wrote to it — see storage.py's
    save_counter_evidence, which had zero callers before this change).

    Returns a small summary dict — never consumed by the probability math,
    purely so the caller can surface real counts on IndependentEvidenceResult:
      {"counter_evidence_count": int, "claim_status_counts": dict[str, int]}
    """
    summary = {"counter_evidence_count": 0, "claim_status_counts": {}}
    if not extracted_claims:
        return summary
    try:
        from types import SimpleNamespace

        from polymarketpulse.claims import (
            detect_claim_contradictions,
            group_claims_by_normalization,
        )
        from polymarketpulse.storage import Storage

        groups = group_claims_by_normalization(tuple(extracted_claims))
        groups, contradiction_pairs = detect_claim_contradictions(groups)
        store = SimpleNamespace(connection=conn)
        for group in groups:
            Storage.save_claim(store, group.canonical_claim)
            Storage.save_claim_group(store, group)
            # Only the canonical claim's own source has a known URL/timestamp
            # here — ClaimGroup.republishing_sources is just source-id
            # strings (no per-source URL), so don't misattribute the
            # canonical claim's URL to other sources' rows.
            Storage.save_claim_source(
                store, group.claim_id, group.canonical_claim.source_id,
                group.canonical_claim.source_url,
                group.canonical_claim.timestamp.isoformat() if group.canonical_claim.timestamp else None,
            )
            for src in group.republishing_sources:
                Storage.save_claim_source(store, group.claim_id, src, None, None)
            summary["claim_status_counts"][group.verification_status] = (
                summary["claim_status_counts"].get(group.verification_status, 0) + 1
            )
        for claim_id_a, claim_id_b in contradiction_pairs:
            # Record both directions — each claim "contradicts" the other.
            Storage.save_counter_evidence(store, claim_id_a, claim_id_b)
            Storage.save_counter_evidence(store, claim_id_b, claim_id_a)
        summary["counter_evidence_count"] = len(contradiction_pairs)
        conn.commit()
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        # Storage-layer or extraction-shape issue — additive persistence
        # only, must never propagate into evidence scoring.
        pass
    return summary


def _first_reported_at(rows: list[tuple]) -> datetime | None:
    timestamps = []
    for row in rows:
        published_at = row[3]
        if not published_at:
            continue
        try:
            dt = datetime.fromisoformat(published_at)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        timestamps.append(dt)
    return min(timestamps) if timestamps else None


def _structured_claim_factors(
    conn: sqlite3.Connection, provider: str, provider_market_id: str, now: datetime,
) -> list[EvidenceFactor]:
    """Real structured claims (GovTrack/IMF PortWatch/etc, via
    claim_market_links -- migration 25) turned into EvidenceFactor objects
    using the SAME reliability/recency/relation math every article-based
    factor already uses -- no separate weighting system.

    Deliberately excludes PATH_STEP claims: those change the resolution
    path (world_state.py), never the yes/no probability directly -- a
    PATH_STEP claim participating here too would be exactly the double
    counting the project owner explicitly asked to be audited against.
    """
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "claim_market_links" not in tables:
            return []
        rows = conn.execute(
            """
            SELECT c.claim_id, c.subject, c.predicate, c.source_id, c.source_url,
                   c.timestamp, c.direction, cml.claim_type
            FROM claim_market_links cml
            JOIN claims c ON c.claim_id = cml.claim_id
            WHERE cml.provider = ? AND cml.provider_market_id = ?
              AND cml.claim_type IN ('DIRECT_RESOLUTION', 'QUANTITATIVE_SIGNAL')
            """,
            (provider, provider_market_id),
        ).fetchall()
    except sqlite3.Error:
        return []

    factors: list[EvidenceFactor] = []
    for claim_id, subject, predicate, source_id, source_url, timestamp, direction, claim_type in rows:
        if timestamp and timestamp > now.isoformat():
            continue  # point-in-time safety: never use a claim from the future
        matched_condition = {"positive": "yes", "negative": "no"}.get(direction)
        is_direct = claim_type == "DIRECT_RESOLUTION"
        relation_label = (
            ("DIRECT_YES" if matched_condition == "yes" else "DIRECT_NO") if is_direct and matched_condition
            else ("SUPPORTS_YES" if matched_condition == "yes" else "SUPPORTS_NO") if matched_condition
            else "CONTEXT"
        )
        factors.append(
            EvidenceFactor(
                news_event_id=0, title=f"{subject}: {predicate}", source=source_id, source_domain=source_id,
                url=source_url or "", published_at=timestamp, reliability=0.95,
                tone=0.0, matched_condition=matched_condition,
                recency_weight=_recency_weight_local(timestamp, now), link_confidence=1.0,
                relation_label=relation_label, entailment="ENTAILS" if matched_condition else "NEUTRAL",
                relation_weight=1.0 if is_direct else 0.6,
                source_type="primary_official", independence_group=source_id,
            )
        )
    return factors


def _structured_path_step_claims(
    conn: sqlite3.Connection, provider: str, provider_market_id: str,
) -> tuple[dict, ...]:
    """Real PATH_STEP claims (GovTrack/etc, via claim_market_links) for
    this market. Returned separately from evidence_for_yes/no by design --
    this is the real data source world_state.py's _derive_resolution_path
    needs to update completed_steps/current_stage, but a PATH_STEP claim
    must never also be counted as yes/no evidence (the double-counting
    guard)."""
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "claim_market_links" not in tables:
            return ()
        rows = conn.execute(
            """
            SELECT c.resolution_step, c.source_id, c.timestamp, c.predicate
            FROM claim_market_links cml
            JOIN claims c ON c.claim_id = cml.claim_id
            WHERE cml.provider = ? AND cml.provider_market_id = ?
              AND cml.claim_type = 'PATH_STEP' AND c.resolution_step IS NOT NULL
            """,
            (provider, provider_market_id),
        ).fetchall()
    except sqlite3.Error:
        return ()
    return tuple(
        {"resolution_step": step, "source": source, "timestamp": ts, "detail": predicate}
        for step, source, ts, predicate in rows
    )


def compute_independent_evidence(
    conn: sqlite3.Connection,
    provider: str,
    provider_market_id: str,
    question: str,
    resolution_text: str | None,
    market_yes_price: float | None,
    now: datetime | None = None,
) -> IndependentEvidenceResult:
    now = now or datetime.now(UTC)

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "news_market_links" not in tables or "news_events" not in tables:
        return _unavailable("Keine News-Infrastruktur in dieser Datenbank vorhanden.")

    # Real structured claims (GovTrack/IMF PortWatch/etc, via
    # claim_market_links) fetched up front so their presence can correctly
    # relax the article-count gates below: a single real, primary,
    # directly-resolution-relevant data point (e.g. IMF PortWatch's real
    # transit count vs the market's own threshold) is categorically
    # stronger evidence than a single ambiguous news article, and must not
    # be blocked by a gate designed for press coverage.
    structured_factors = _structured_claim_factors(conn, provider, provider_market_id, now)
    path_step_claims = _structured_path_step_claims(conn, provider, provider_market_id)
    has_direct_structured_evidence = any(
        f.matched_condition is not None and f.relation_label in ("DIRECT_YES", "DIRECT_NO")
        for f in structured_factors
    )

    # Point-in-time safety: only news published at or before `now` may ever
    # be used. `now` defaults to wall-clock time for the live/normal call
    # path, but backtesting callers pass a real historical forecast_time
    # here, which makes this the load-bearing look-ahead guard for the
    # whole evidence pipeline. Rows with a NULL/unparseable published_at
    # are excluded rather than assumed safe, both live and in backtests.
    rows = conn.execute(
        """
        SELECT ne.id, ne.title, ne.source, ne.published_at, nml.confidence, ne.source_url
        FROM news_market_links nml
        JOIN news_events ne ON ne.id = nml.news_event_id
        WHERE nml.provider = ? AND nml.provider_market_id = ?
          AND ne.published_at IS NOT NULL AND ne.published_at <= ?
        ORDER BY ne.published_at DESC
        """,
        (provider, provider_market_id, now.isoformat()),
    ).fetchall()

    if len(rows) == 0 and not has_direct_structured_evidence:
        return _unavailable(
            "keine unabhängige Schätzung möglich — zu wenige verknüpfte öffentliche Primärquellen "
            f"(0 gefunden, mindestens {MIN_EVIDENCE_ITEMS_FOR_ESTIMATE} nötig).",
            path_step_claims=path_step_claims,
        )
    # A real root-cause fix (not previously diagnosed): with fewer than
    # MIN_EVIDENCE_ITEMS_FOR_ESTIMATE *linked* rows (most commonly exactly
    # 1 — e.g. Hormuz), this function used to return unavailable BEFORE the
    # per-article loop below ever ran, which meant claim extraction/
    # persistence (_persist_claims_for_event/_persist_claim_groups) never
    # executed for these markets either, even though that persistence is
    # explicitly documented as unconditional ("regardless of whether an
    # independent probability ends up computable"). That contract was only
    # honored for markets with >=2 linked rows. Markets with exactly 1
    # linked article never got a real claim extracted or persisted purely
    # because of this early return — a genuine "sources fetched but claims
    # never generated" gap, not a data-scarcity limitation. Fixed by always
    # letting the loop run when there is at least 1 row; the two SEPARATE,
    # unchanged probability-computation gates below (line ~423 equivalent
    # now folded into the `len(scored) < MIN...` check, and the historical
    # 2-row check preserved via `insufficient_rows`) still make an estimate
    # unavailable on thin evidence — this only unblocks claim persistence,
    # never the probability math.
    insufficient_rows = len(rows) < MIN_EVIDENCE_ITEMS_FOR_ESTIMATE and not has_direct_structured_evidence

    # Legacy resolution-condition term lists — still consulted as a
    # defense-in-depth secondary check (see SENTIMENT_FALLBACK_MIN_RELEVANCE
    # below), but the primary classification now comes from
    # semantics.classify_evidence_relation, which reasons about what the
    # event actually *is* (entailment) rather than the article's tone.
    yes_terms, no_terms, subject_terms = parse_resolution_conditions(question, resolution_text)
    del subject_terms  # reserved for future relevance filtering; not yet used to exclude evidence

    proposition = parse_market_proposition(question, resolution_text)

    factors: list[EvidenceFactor] = []
    collected_claims: list = []
    # ROUND-2 (section 6, Event Intelligence): parallel to collected_claims
    # above, but keyed by which side of the proposition each successfully-
    # extracted claim supports — needed below to dedupe "N syndicated
    # articles about the same underlying event" down to their real distinct
    # event count before they reach confirmation_count. See the dedup
    # block after this loop for the full rationale.
    claims_for_yes: list = []
    claims_for_no: list = []
    for event_id, title, source, published_at, link_confidence, source_url in rows:
        from urllib.parse import urlparse

        domain = urlparse(source_url).netloc if source_url else ""
        reliability = _domain_reliability(source, domain)
        sentiment, _matched_terms = score_sentiment(title)
        recency = _recency_weight_local(published_at, now, event_type=proposition.event_type)

        title_lower = title.lower()
        event = extract_event(title)
        relation = classify_evidence_relation(proposition, event, sentiment, link_confidence, title=title)
        # Phase H: persist the structured event alongside the market it was
        # scored for (provenance: source, certainty, timestamp) — purely
        # additive, does not affect any of the scoring below.
        _persist_extracted_event(conn, provider, provider_market_id, title, event, event_id, published_at, source)
        # Additive claims-wiring (Task 1): convert the same already-computed
        # event into a claim, collected here and grouped/persisted once
        # below (real cross-article dedup) — never consumed by the
        # scoring math in this function.
        extracted_claim = _persist_claims_for_event(conn, event, event_id, source, source_url, published_at)
        if extracted_claim is not None:
            # BLOCK C, Part 1/2: wire a real (often-unknown) resolution_step
            # reference onto claims for markets whose event_type has a known
            # multi-step resolution structure (world_state.py's
            # ResolutionStep/ResolutionPath). Only fires when this article's
            # title actually contains a recognized step keyword — most
            # claims stay resolution_step=None, honestly, since
            # extract_claim_from_event's own predicate_map only recognizes a
            # narrow set of event actions unrelated to legislative steps.
            if proposition.event_type == "legislation":
                import dataclasses as _dc

                from .world_state import _classify_legislation_step

                classified = _classify_legislation_step(title)
                if classified is not None:
                    extracted_claim = _dc.replace(extracted_claim, resolution_step=classified[0])
            collected_claims.append(extracted_claim)

        # Sentiment is never allowed to promote a relation past the WEAK
        # tier here — classify_evidence_relation already enforces this
        # internally, but SENTIMENT_FALLBACK_MIN_RELEVANCE is kept as a
        # second, independent gate on the WEAK tier specifically (defense
        # in depth: even a semantics bug can't resurrect the original
        # "tone about the subject == evidence" failure mode).
        relation_label = relation.label
        relation_weight = relation.quantitative_weight
        if relation_label in ("WEAK_YES", "WEAK_NO") and link_confidence < SENTIMENT_FALLBACK_MIN_RELEVANCE:
            relation_label = "AMBIGUOUS"
            relation_weight = 0.0

        matched_condition: str | None = None
        if relation_label in ("DIRECT_YES", "SUPPORTS_YES", "WEAK_YES"):
            matched_condition = "yes"
        elif relation_label in ("DIRECT_NO", "SUPPORTS_NO", "WEAK_NO"):
            matched_condition = "no"

        # Explicit "resolves YES/NO if ..." term matches from the market's
        # own resolution text are on-topic by construction (not incidental
        # keyword overlap) — if present and the semantics layer found
        # nothing else, they still count as full-weight direct evidence.
        if matched_condition is None:
            if yes_terms and any(t in title_lower for t in yes_terms):
                matched_condition = "yes"
                relation_label, relation_weight = "DIRECT_YES", 1.0
            elif no_terms and any(t in title_lower for t in no_terms):
                matched_condition = "no"
                relation_label, relation_weight = "DIRECT_NO", 1.0

        # BLOCK C, Part 1: resolve the registry entry via the SAME
        # `_resolve_cluster_key` helper source_registry.py's own
        # `calculate_source_quality_score`/`_cluster_sources` already use
        # (prefer the domain, fall back to the curated source label) rather
        # than looking the raw URL domain up directly. Audit finding: a
        # direct `get_source_definition(domain)` lookup here almost never
        # matches — SOURCE_REGISTRY is keyed by short labels ("reuters",
        # "apnews"), not URL netlocs ("reuters.com", "apnews.com") — so
        # `independence_group`/`source_type` were effectively always
        # "OTHER"/None for real article domains even though the registry DID
        # know the source under its curated label. This made
        # `independence_group` a real SCAFFOLD-vs-CONNECTED gap: computed
        # and displayed on every factor, but silently dead for the vast
        # majority of real evidence.
        _resolved_key = _resolve_cluster_key(domain, source)
        _source_def = get_source_definition(_resolved_key)
        factors.append(
            EvidenceFactor(
                news_event_id=event_id, title=title, source=source, source_domain=domain,
                url=source_url or "", published_at=published_at, reliability=reliability,
                tone=sentiment, matched_condition=matched_condition, recency_weight=recency,
                link_confidence=link_confidence, relation_label=relation_label,
                entailment=relation.entailment, relation_weight=relation_weight,
                source_type=_source_def.source_type.value if _source_def else "OTHER",
                independence_group=_source_def.independence_group if _source_def else None,
            )
        )
        if extracted_claim is not None:
            if matched_condition == "yes":
                claims_for_yes.append(extracted_claim)
            elif matched_condition == "no":
                claims_for_no.append(extracted_claim)

    # Persist/dedup claims collected from all linked articles regardless of
    # whether an independent probability ends up computable below — this is
    # evidence infrastructure (stable IDs, future verification tracking),
    # not a probability input, so it must not gate on the scoring outcome.
    claim_summary = _persist_claim_groups(conn, collected_claims)

    # Real structured-claim integration (GovTrack/IMF PortWatch/etc, via
    # claim_market_links -- migration 25): only DIRECT_RESOLUTION and
    # QUANTITATIVE_SIGNAL claims are folded in here as real evidence
    # factors, reusing the EXACT same scoring math every article-based
    # factor already goes through (yes/no domain dedup, confirmation_count,
    # weighted average, MIN_EVIDENCE_ITEMS_FOR_ESTIMATE gate) -- no
    # parallel weighting system, no invented coefficients. PATH_STEP claims
    # are deliberately excluded here: they change the resolution path
    # (world_state.py), not the yes/no probability directly, so they must
    # never also count as evidence -- this is the real double-counting
    # guard the project owner asked for.
    factors = list(factors) + structured_factors

    evidence_for_yes = tuple(f for f in factors if f.matched_condition == "yes")
    evidence_for_no = tuple(f for f in factors if f.matched_condition == "no")
    # I2 (additive): items seen but not matched to either condition
    # (CONTEXT/IRRELEVANT/AMBIGUOUS/gated-WEAK, relation_weight 0) — never
    # fed into the estimate, but kept visible for transparency.
    discarded_evidence = tuple(f for f in factors if f.matched_condition is None)

    yes_domains = {f.source_domain or f.source for f in evidence_for_yes}
    no_domains = {f.source_domain or f.source for f in evidence_for_no}
    contradiction = bool(yes_domains) and bool(no_domains)

    # BLOCK C, Part 1 (reconciliation): when NO per-event claim information
    # is available at all (extract_claim_from_event only recognizes a
    # narrow set of event actions — see claims.py — so this is common), a
    # raw distinct-domain count is not the right fallback either —
    # source_registry.py's own independence_group already knows, e.g., that
    # reuters.com and apnews.com are the SAME wire-service cluster
    # ("reuters_ap"), not two independent confirmations.
    # EvidenceFactor.independence_group was already being computed and
    # attached to every factor (see the loop above) but was a real
    # SCAFFOLD-vs-CONNECTED gap: displayed on the factor for the UI, never
    # actually fed into confirmation_count. Used ONLY as the no-claims
    # fallback (not as a blanket cap alongside the real per-event claim-
    # group count below) — collapsing by source cluster is correct within
    # a single reported event, but wrongly conflates genuinely DIFFERENT
    # events reported by sister wire services (e.g. Reuters covering one
    # resignation, AP covering an unrelated one) if applied across an
    # entire market side; the real event_count from claim-group dedup
    # already handles that distinction correctly when claims exist, so the
    # cluster-collapsed count is only trusted when nothing more precise is
    # available.
    yes_clusters = {f.independence_group or f.source_domain or f.source for f in evidence_for_yes}
    no_clusters = {f.independence_group or f.source_domain or f.source for f in evidence_for_no}

    # ROUND-2 (section 6, Event Intelligence): domain count alone answers
    # "how many distinct outlets published a yes/no-matched article", not
    # "how many genuinely distinct underlying events/claims were reported" —
    # a single wire story syndicated verbatim to N different domains would
    # otherwise inflate confirmation_count (and, downstream, the Bayesian
    # update's confidence and information_edge_score) to N even though it is
    # ONE real-world event, not N independent confirmations. Audit finding:
    # claims.py's group_claims_by_normalization/ClaimGroup already computes
    # exactly this real per-event dedup and IS invoked on every call
    # (_persist_claim_groups, above) — but its output was previously only
    # used for persistence side effects (claim_status_counts) and silently
    # discarded otherwise, never reaching this confirmation_count. Fixed
    # here by capping each side's raw domain-based count at its real
    # distinct-claim-group count whenever claims were actually extracted for
    # that side, and at its independence-cluster count otherwise (this is a
    # conservative MIN in both cases, never an increase past the raw domain
    # count, and never invents a count when no claims/clustering signal
    # exists at all).
    yes_event_count = (
        len(group_claims_by_normalization(tuple(claims_for_yes))) if claims_for_yes else len(yes_clusters)
    )
    no_event_count = (
        len(group_claims_by_normalization(tuple(claims_for_no))) if claims_for_no else len(no_clusters)
    )
    confirmation_count = max(min(len(yes_domains), yes_event_count), min(len(no_domains), no_event_count))

    if insufficient_rows:
        # Claims (if any) were already extracted/persisted by the loop
        # above — this only withholds the probability estimate, which is
        # correct and unchanged: 1 linked article is still not enough
        # independent confirmation to move a probability.
        return _unavailable(
            "keine unabhängige Schätzung möglich — zu wenige verknüpfte öffentliche Primärquellen "
            f"({len(rows)} gefunden, mindestens {MIN_EVIDENCE_ITEMS_FOR_ESTIMATE} nötig). "
            f"{len(rows)} relevante Quelle(n) vorhanden; unabhängige Bestätigung fehlt.",
            factors=tuple(factors), path_step_claims=path_step_claims,
        )

    scored = [f for f in factors if f.matched_condition is not None]
    if len(scored) < MIN_EVIDENCE_ITEMS_FOR_ESTIMATE and not has_direct_structured_evidence:
        # This is the real fix, not just the earlier `len(rows) < MIN...`
        # check: having 2+ *linked* articles is not the same as having 2+
        # articles that actually say something about the resolution
        # condition. A single on-topic (or loosely-relevant-but-toned)
        # headline must never be enough to move the estimate on its own —
        # "no data" must stay "no data" (unavailable), not collapse into a
        # confident-looking number built from one weak signal. The
        # has_direct_structured_evidence carve-out is deliberately narrow:
        # only a real DIRECT_RESOLUTION structured claim (an official,
        # primary, directly-resolution-relevant data point -- not a
        # PATH_STEP or generic news factor) can satisfy this alone.
        return _unavailable(
            f"keine unabhängige Schätzung möglich — nur {len(scored)} Nachrichtentreffer mit erkennbarem "
            f"Bezug zur Resolution-Bedingung (mindestens {MIN_EVIDENCE_ITEMS_FOR_ESTIMATE} nötig), "
            f"von {len(rows)} verknüpften Quellen insgesamt.",
            factors=tuple(factors), path_step_claims=path_step_claims,
        )

    # relation_weight (0..1, from semantics.classify_evidence_relation) is
    # the entailment-strength multiplier: DIRECT_*/SUPPORTS_* evidence
    # weighs close to its full reliability*recency*link_confidence product,
    # WEAK_* evidence (tone-only, gated) contributes only a fraction, and
    # CONTEXT/IRRELEVANT/AMBIGUOUS evidence (relation_weight == 0) never
    # reaches this point at all (filtered out of `scored` above).
    weight_sum = sum(f.reliability * f.recency_weight * f.link_confidence * f.relation_weight for f in scored)
    if weight_sum <= 0:
        return _unavailable(
            "keine unabhängige Schätzung möglich — Quellvertrauen/Aktualität zu gering.",
            path_step_claims=path_step_claims,
        )

    direction_sum = sum(
        (1.0 if f.matched_condition == "yes" else -1.0)
        * f.reliability * f.recency_weight * f.link_confidence * f.relation_weight
        for f in scored
    )
    weighted_direction = round(direction_sum / weight_sum, 4)  # -1..+1

    # Independent probability starts from a neutral 0.5 prior — deliberately
    # NOT the market price — and is moved only by the evidence itself. The
    # evidence's own average relevance (term-overlap link confidence) additionally
    # scales how far it's allowed to move that prior — a handful of only
    # loosely-relevant (but strongly-toned) articles must produce a much
    # smaller swing than the same count of directly on-topic ones, even
    # after the relevance gate above.
    average_relevance = sum(f.link_confidence for f in scored) / len(scored)
    bayes = bayesian_update(
        prior_probability=0.5, weighted_news_sentiment=weighted_direction,
        confirmation_count=confirmation_count, news_weight_multiplier=average_relevance,
    )
    independent_yes_probability = bayes.posterior_probability

    # --- Extraordinary-event guard (Phase B3) -----------------------------
    extraordinary_guard_applied = False
    extraordinary_guard_detail: str | None = None
    if proposition.event_type in EXTRAORDINARY_EVENT_TYPES:
        direct_count = sum(1 for f in scored if f.relation_label in ("DIRECT_YES", "DIRECT_NO"))
        if direct_count < EXTRAORDINARY_DIRECT_EVIDENCE_REQUIRED:
            base_rate = get_base_rate(proposition.event_type)
            # No defensible base rate for this extraordinary type: anchor to
            # the same neutral 0.5 the Bayesian update above already started
            # from (not a fresh fallback — just tightening how far that
            # existing prior is allowed to move on weak evidence).
            anchor = base_rate if base_rate is not None else 0.5
            max_swing = _EXTRAORDINARY_MAX_SWING_BY_DIRECT_COUNT.get(direct_count, 0.08)
            raw = independent_yes_probability
            dampened = max(anchor - max_swing, min(anchor + max_swing, raw))
            if abs(dampened - raw) > 1e-9:
                extraordinary_guard_applied = True
                extraordinary_guard_detail = (
                    f"Extraordinary-event guard fired for event_type='{proposition.event_type}': only "
                    f"{direct_count} DIRECT_YES/DIRECT_NO-tier evidence item(s) found "
                    f"(< {EXTRAORDINARY_DIRECT_EVIDENCE_REQUIRED} required to move freely). Raw estimate "
                    f"{raw:.1%} dampened to {dampened:.1%}, clamped within +/-{max_swing:.0%} of anchor "
                    f"{anchor:.1%} ({'base rate' if base_rate is not None else 'neutral prior, no base rate available'})."
                )
                independent_yes_probability = round(dampened, 4)

    source_quality_score = round(min(100.0, (weight_sum / len(scored)) * 100), 1)
    
    # Phase F: Source Registry Integration - echte Quality und Independence
    source_domains = [f.source_domain or f.source for f in scored]
    source_labels = [f.source for f in scored]
    # Berechne Quality aus Source Registry (domain preferred, curated label
    # as fallback so a known official/wire-service label with an
    # unrecognized or placeholder domain still gets real trust credit).
    source_quality_score = calculate_source_quality_score(source_domains, source_labels)

    first_reported = _first_reported_at(rows)
    time_since_first_report_hours = None
    if first_reported is not None:
        time_since_first_report_hours = round((now - first_reported).total_seconds() / 3600, 1)

    breaking = time_since_first_report_hours is not None and time_since_first_report_hours <= BREAKING_WINDOW_HOURS

    divergence = None
    information_edge_score = None
    not_yet_priced_in: tuple[EvidenceFactor, ...] = ()
    if market_yes_price is not None:
        divergence = round(independent_yes_probability - market_yes_price, 4)
        magnitude = abs(divergence)
        information_edge_score = round(
            min(100.0, magnitude * 200 * (0.5 + 0.5 * min(confirmation_count, 5) / 5) * (source_quality_score / 100)),
            1,
        )
        if breaking and magnitude >= 0.05:
            not_yet_priced_in = scored

    detail = (
        f"{len(scored)} unabhängige(r) Nachrichtentreffer ausgewertet ({len(yes_domains)} für YES-Bedingung, "
        f"{len(no_domains)} für NO-Bedingung), {confirmation_count} unabhängig bestätigende(r) Domain(s), "
        f"gewichtete Richtung {weighted_direction:+.2f}. " + bayes.detail
    )
    if contradiction:
        detail += " Widersprüchliche Quellenlage erkannt (sowohl YES- als auch NO-Evidenz vorhanden)."
    if extraordinary_guard_detail:
        detail += " " + extraordinary_guard_detail

    return IndependentEvidenceResult(
        available=True,
        independent_yes_probability=independent_yes_probability,
        confirmation_count=confirmation_count,
        source_quality_score=source_quality_score,
        time_since_first_report_hours=time_since_first_report_hours,
        contradiction_detected=contradiction,
        breaking=breaking,
        information_edge_score=information_edge_score,
        divergence=divergence,
        evidence_for_yes=evidence_for_yes,
        evidence_for_no=evidence_for_no,
        not_yet_priced_in=not_yet_priced_in,
        discarded_evidence=discarded_evidence,
        detail=detail,
        extraordinary_guard_applied=extraordinary_guard_applied,
        extraordinary_guard_detail=extraordinary_guard_detail,
        counter_evidence_count=claim_summary.get("counter_evidence_count", 0),
        claim_status_counts=claim_summary.get("claim_status_counts", {}),
        path_step_claims=path_step_claims,
    )


def _recency_weight_local(published_at: str | None, now: datetime, event_type: str | None = None) -> float:
    """Same shape as prediction/news.py's `_recency_weight`, but with this
    module's faster 24h DEFAULT half-life — kept as a thin local wrapper
    rather than parameterizing the shared one, to not change existing
    news.py behavior.

    `event_type` (optional, defaults to None) selects a category-aware
    half-life from `EVENT_TYPE_RECENCY_HALF_LIFE_HOURS` when the market's
    proposition has a recognized event_type (legislative/geopolitical/macro
    — see that dict's docstring for the real reasoning per category);
    anything else falls back to the unchanged default curve, so every
    existing caller/behavior for uncategorized evidence is unaffected."""
    if not published_at:
        return 0.3
    try:
        published = datetime.fromisoformat(published_at)
    except ValueError:
        return 0.3
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    hours_ago = max(0.0, (now - published).total_seconds() / 3600)
    half_life = EVENT_TYPE_RECENCY_HALF_LIFE_HOURS.get(event_type, RECENCY_HALF_LIFE_HOURS) if event_type else RECENCY_HALF_LIFE_HOURS
    return round(0.5 ** (hours_ago / half_life), 4)
