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
RECENCY_HALF_LIFE_HOURS = 24.0
BREAKING_WINDOW_HOURS = 48.0
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

    def as_dict(self) -> dict:
        return {
            "title": self.title, "source": self.source, "source_domain": self.source_domain,
            "url": self.url, "published_at": self.published_at, "reliability": self.reliability,
            "tone": self.tone, "matched_condition": self.matched_condition,
            "recency_weight": self.recency_weight, "relation_label": self.relation_label,
            "entailment": self.entailment, "relation_weight": self.relation_weight,
            # I2 (additive): topical-match relevance, previously computed but
            # never surfaced past the internal scoring math.
            "link_confidence": self.link_confidence,
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
        }


def _unavailable(detail: str) -> IndependentEvidenceResult:
    return IndependentEvidenceResult(
        available=False, independent_yes_probability=None, confirmation_count=0,
        source_quality_score=None, time_since_first_report_hours=None,
        contradiction_detected=False, breaking=False, information_edge_score=None,
        divergence=None, detail=detail,
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


def _persist_claim_groups(conn: sqlite3.Connection, extracted_claims: list) -> None:
    """Group and persist claims collected across all linked articles for
    one market (real deduplication when the same underlying claim shows up
    from multiple sources) — additive only, wrapped so a storage-layer
    issue never propagates into evidence scoring."""
    if not extracted_claims:
        return
    try:
        from types import SimpleNamespace

        from polymarketpulse.claims import group_claims_by_normalization
        from polymarketpulse.storage import Storage

        groups = group_claims_by_normalization(tuple(extracted_claims))
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
        conn.commit()
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        # Storage-layer or extraction-shape issue — additive persistence
        # only, must never propagate into evidence scoring.
        pass


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

    rows = conn.execute(
        """
        SELECT ne.id, ne.title, ne.source, ne.published_at, nml.confidence, ne.source_url
        FROM news_market_links nml
        JOIN news_events ne ON ne.id = nml.news_event_id
        WHERE nml.provider = ? AND nml.provider_market_id = ?
        ORDER BY ne.published_at DESC
        """,
        (provider, provider_market_id),
    ).fetchall()

    if len(rows) < MIN_EVIDENCE_ITEMS_FOR_ESTIMATE:
        return _unavailable(
            "keine unabhängige Schätzung möglich — zu wenige verknüpfte öffentliche Primärquellen "
            f"({len(rows)} gefunden, mindestens {MIN_EVIDENCE_ITEMS_FOR_ESTIMATE} nötig)."
        )

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
    for event_id, title, source, published_at, link_confidence, source_url in rows:
        from urllib.parse import urlparse

        domain = urlparse(source_url).netloc if source_url else ""
        reliability = _domain_reliability(source, domain)
        sentiment, _matched_terms = score_sentiment(title)
        recency = _recency_weight_local(published_at, now)

        title_lower = title.lower()
        event = extract_event(title)
        relation = classify_evidence_relation(proposition, event, sentiment, link_confidence)
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

        factors.append(
            EvidenceFactor(
                news_event_id=event_id, title=title, source=source, source_domain=domain,
                url=source_url or "", published_at=published_at, reliability=reliability,
                tone=sentiment, matched_condition=matched_condition, recency_weight=recency,
                link_confidence=link_confidence, relation_label=relation_label,
                entailment=relation.entailment, relation_weight=relation_weight,
            )
        )

    # Persist/dedup claims collected from all linked articles regardless of
    # whether an independent probability ends up computable below — this is
    # evidence infrastructure (stable IDs, future verification tracking),
    # not a probability input, so it must not gate on the scoring outcome.
    _persist_claim_groups(conn, collected_claims)

    evidence_for_yes = tuple(f for f in factors if f.matched_condition == "yes")
    evidence_for_no = tuple(f for f in factors if f.matched_condition == "no")
    # I2 (additive): items seen but not matched to either condition
    # (CONTEXT/IRRELEVANT/AMBIGUOUS/gated-WEAK, relation_weight 0) — never
    # fed into the estimate, but kept visible for transparency.
    discarded_evidence = tuple(f for f in factors if f.matched_condition is None)

    yes_domains = {f.source_domain or f.source for f in evidence_for_yes}
    no_domains = {f.source_domain or f.source for f in evidence_for_no}
    contradiction = bool(yes_domains) and bool(no_domains)
    confirmation_count = max(len(yes_domains), len(no_domains))

    scored = [f for f in factors if f.matched_condition is not None]
    if len(scored) < MIN_EVIDENCE_ITEMS_FOR_ESTIMATE:
        # This is the real fix, not just the earlier `len(rows) < MIN...`
        # check: having 2+ *linked* articles is not the same as having 2+
        # articles that actually say something about the resolution
        # condition. A single on-topic (or loosely-relevant-but-toned)
        # headline must never be enough to move the estimate on its own —
        # "no data" must stay "no data" (unavailable), not collapse into a
        # confident-looking number built from one weak signal.
        return _unavailable(
            f"keine unabhängige Schätzung möglich — nur {len(scored)} Nachrichtentreffer mit erkennbarem "
            f"Bezug zur Resolution-Bedingung (mindestens {MIN_EVIDENCE_ITEMS_FOR_ESTIMATE} nötig), "
            f"von {len(rows)} verknüpften Quellen insgesamt."
        )

    # relation_weight (0..1, from semantics.classify_evidence_relation) is
    # the entailment-strength multiplier: DIRECT_*/SUPPORTS_* evidence
    # weighs close to its full reliability*recency*link_confidence product,
    # WEAK_* evidence (tone-only, gated) contributes only a fraction, and
    # CONTEXT/IRRELEVANT/AMBIGUOUS evidence (relation_weight == 0) never
    # reaches this point at all (filtered out of `scored` above).
    weight_sum = sum(f.reliability * f.recency_weight * f.link_confidence * f.relation_weight for f in scored)
    if weight_sum <= 0:
        return _unavailable("keine unabhängige Schätzung möglich — Quellvertrauen/Aktualität zu gering.")

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
    )


def _recency_weight_local(published_at: str | None, now: datetime) -> float:
    """Same shape as prediction/news.py's `_recency_weight`, but with this
    module's faster 24h half-life — kept as a thin local wrapper rather than
    parameterizing the shared one, to not change existing news.py behavior."""
    if not published_at:
        return 0.3
    try:
        published = datetime.fromisoformat(published_at)
    except ValueError:
        return 0.3
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    hours_ago = max(0.0, (now - published).total_seconds() / 3600)
    return round(0.5 ** (hours_ago / RECENCY_HALF_LIFE_HOURS), 4)
