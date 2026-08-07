"""Event/Entity/Relation foundation — the first concrete slice of the
long-term "Event Graph" / causal-reasoning vision. Deliberately minimal:
entity resolution (alias -> canonical entity), an event-to-market relevance
score, and an evidence-tier vocabulary that gates whether a relation is
allowed to carry any quantitative weight at all.

Hard rule this module exists to enforce: a plausible-sounding causal story
is not evidence. Only KNOWN/STRONG_EVIDENCE/SUPPORTED relations may ever
get a nonzero forecast weight — PLAUSIBLE/SPECULATIVE/UNKNOWN relations may
be shown for explainability but must never move a probability.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .news.classifier import _STOPWORDS, _WORD_RE


def _significant_terms(text: str, max_terms: int = 20) -> tuple[str, ...]:
    """Same small, auditable heuristic as prediction/resolution_rules.py's
    helper of the same name — duplicated rather than imported to avoid a
    circular import (prediction/event_relations.py imports from this
    module)."""
    words = [w.lower() for w in _WORD_RE.findall(text)]
    significant = [w for w in words if len(w) > 3 and w not in _STOPWORDS]
    seen: set[str] = set()
    ordered: list[str] = []
    for word in significant:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return tuple(ordered[:max_terms])

# --- Evidence tiers ----------------------------------------------------

TIER_KNOWN = "KNOWN"
TIER_STRONG_EVIDENCE = "STRONG_EVIDENCE"
TIER_SUPPORTED = "SUPPORTED"
TIER_PLAUSIBLE = "PLAUSIBLE"
TIER_SPECULATIVE = "SPECULATIVE"
TIER_UNKNOWN = "UNKNOWN"

ALL_TIERS = (TIER_KNOWN, TIER_STRONG_EVIDENCE, TIER_SUPPORTED, TIER_PLAUSIBLE, TIER_SPECULATIVE, TIER_UNKNOWN)
# Only these tiers may ever carry nonzero quantitative forecast weight.
QUANTITATIVE_TIERS = frozenset({TIER_KNOWN, TIER_STRONG_EVIDENCE, TIER_SUPPORTED})

RELATION_TYPES = (
    "INCREASES", "DECREASES", "ENABLES", "PREVENTS", "PRECEDES",
    "CORRELATES_WITH", "CAUSES", "CONTRIBUTES_TO", "SIGNALS",
)
# CAUSES requires strong evidence by construction — never assign it at a
# weak tier. Enforced in `validate_relation_tier`, not just documented.
STRONG_CAUSAL_RELATION_TYPES = frozenset({"CAUSES"})

GEO_SCOPES = ("global", "country", "state", "city", "venue", "region")

# A tiny, hand-curated seed of common aliases. Real entity resolution
# (NLP-based, growing from observed news text) is future work — this is
# enough to make the mechanism testable and demonstrably correct today.
DEFAULT_ENTITY_SEEDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "germany": ("country", "germany", ("germany", "deutschland", "german national team", "dfb team", "die mannschaft")),
    "munich": ("city", "germany", ("munich", "münchen", "city of munich")),
    "strait of hormuz": ("region", "global", ("strait of hormuz", "hormuz strait", "straße von hormus", "straits of hormuz")),
}


def validate_relation_tier(relation_type: str, evidence_tier: str) -> None:
    if relation_type in STRONG_CAUSAL_RELATION_TYPES and evidence_tier not in (TIER_KNOWN, TIER_STRONG_EVIDENCE):
        raise ValueError(
            f"relation_type={relation_type!r} requires evidence_tier KNOWN or STRONG_EVIDENCE, got {evidence_tier!r}."
        )
    if evidence_tier not in ALL_TIERS:
        raise ValueError(f"Unknown evidence_tier: {evidence_tier!r}")


def quantitative_weight_for_tier(evidence_tier: str) -> float:
    """0.0 for any non-quantitative tier — the single choke point that
    keeps PLAUSIBLE/SPECULATIVE relations display-only."""
    return 1.0 if evidence_tier in QUANTITATIVE_TIERS else 0.0


# --- Entity resolution ---------------------------------------------------

def seed_default_entities(conn: sqlite3.Connection) -> None:
    """Idempotent: inserts the curated alias seed set if not already present."""
    for canonical, (entity_type, geo, aliases) in DEFAULT_ENTITY_SEEDS.items():
        row = conn.execute("SELECT id FROM entities WHERE canonical_name = ?", (canonical,)).fetchone()
        entity_id = row[0] if row else None
        if entity_id is None:
            cursor = conn.execute(
                "INSERT INTO entities (canonical_name, entity_type, geographic_scope) VALUES (?, ?, ?)",
                (canonical, entity_type, geo),
            )
            entity_id = cursor.lastrowid
        for alias in aliases:
            conn.execute(
                "INSERT INTO entity_aliases (entity_id, alias) VALUES (?, ?) ON CONFLICT(alias) DO NOTHING",
                (entity_id, alias.lower()),
            )
    conn.commit()


def resolve_entity(conn: sqlite3.Connection, raw_name: str) -> str | None:
    """Returns the canonical entity name for a raw mention, or None if
    unresolvable. Never fabricates a match — an unknown term stays unknown
    rather than being guessed at."""
    lowered = raw_name.strip().lower()
    row = conn.execute(
        "SELECT e.canonical_name FROM entity_aliases a JOIN entities e ON e.id = a.entity_id WHERE a.alias = ?",
        (lowered,),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT canonical_name FROM entities WHERE canonical_name = ?", (lowered,)).fetchone()
    return row[0] if row else None


# --- Event-to-market relevance -------------------------------------------

_GEO_DECAY = {
    ("global", "global"): 1.0, ("country", "country"): 1.0, ("city", "city"): 1.0, ("venue", "venue"): 1.0,
    ("region", "region"): 1.0,
    ("city", "country"): 0.5, ("city", "global"): 0.15,
    ("country", "global"): 0.4, ("region", "global"): 0.3,
}


def _geo_relevance(event_geo: str | None, market_geo: str | None) -> float:
    if not event_geo or not market_geo:
        return 0.5  # unknown geography — neutral, not zero (don't punish missing metadata) and not full credit
    if event_geo == market_geo:
        return 1.0
    return _GEO_DECAY.get((event_geo, market_geo)) or _GEO_DECAY.get((market_geo, event_geo)) or 0.2


@dataclass(frozen=True)
class EventMarketRelevance:
    relevance_score: float  # 0..1
    entity_overlap: float
    geographic_relevance: float
    detail: str

    def as_dict(self) -> dict:
        return {
            "relevance_score": self.relevance_score, "entity_overlap": self.entity_overlap,
            "geographic_relevance": self.geographic_relevance, "detail": self.detail,
        }


def compute_event_market_relevance(
    event_title: str, market_question: str,
    event_geo: str | None = None, market_geo: str | None = None,
) -> EventMarketRelevance:
    """Term-overlap + geographic-decay relevance score — the same
    conservative, auditable style as cross_market.py's relation detection.
    Never claims relevance without shared, significant terms."""
    event_terms = set(_significant_terms(event_title, max_terms=20))
    market_terms = set(_significant_terms(market_question, max_terms=20))
    if not event_terms or not market_terms:
        return EventMarketRelevance(0.0, 0.0, 0.0, "Keine auswertbaren Begriffe für einen Relevanzvergleich.")

    shared = event_terms & market_terms
    entity_overlap = len(shared) / min(len(event_terms), len(market_terms))
    geo = _geo_relevance(event_geo, market_geo)
    relevance = round(entity_overlap * geo, 4)

    detail = (
        f"{len(shared)} gemeinsame(r) Begriff(e) ({', '.join(sorted(shared)) or '-'}), "
        f"Entity-Overlap {entity_overlap:.0%}, geografische Relevanz {geo:.0%}."
    )
    return EventMarketRelevance(relevance_score=relevance, entity_overlap=round(entity_overlap, 4), geographic_relevance=geo, detail=detail)
