"""News submodel — deterministic, lexicon-based sentiment scoring over
already-fetched and already-linked news (`news_events` / `news_market_links`,
populated by `news/` — no live fetching happens here). Intentionally **not**
an LLM: per the project's core rule, nothing that feeds the final
probability may come from a generative model. GPT is only ever handed the
*output* of this module afterward, to phrase it — never to produce it.

Sentiment is a simple keyword/polarity lexicon (transparent, extensible,
auditable) rather than a trained classifier — every score is traceable to
the exact matched terms.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from .types import SubmodelEstimate

# Deliberately small, auditable polarity lexicon (German + English, since
# feeds mix languages). Extend by adding words — no retraining involved.
_POSITIVE_TERMS = (
    "confirmed", "confirms", "wins", "win", "victory", "approved", "approves", "success", "successful",
    "agreement", "deal reached", "signed", "passed", "surge", "advances", "advance", "positive",
    "bestätigt", "sieg", "gewinnt", "erfolgreich", "einigung", "genehmigt", "durchbruch", "positiv",
)
_NEGATIVE_TERMS = (
    "denied", "denies", "rejected", "rejects", "fails", "failed", "failure", "cancelled", "canceled",
    "collapse", "collapses", "delay", "delayed", "postponed", "withdraws", "withdrawn", "negative",
    "loses", "lose", "defeat", "dementiert", "abgelehnt", "gescheitert", "verzögert", "abgesagt", "niederlage",
)

# Per-domain trust weight (0..1). Unknown sources default to 0.5 (neutral
# trust) — never silently 0 (would erase the source) or 1 (would over-trust
# an unrecognized outlet). Configurable constant, not learned.
_SOURCE_TRUST: dict[str, float] = {
    "reuters": 0.95, "apnews": 0.95, "ap news": 0.95, "bloomberg": 0.9, "bbc": 0.9,
    "wsj": 0.85, "nytimes": 0.85, "the guardian": 0.8, "cnbc": 0.8, "polymarket": 0.6,
}

RECENCY_HALF_LIFE_HOURS = 48.0  # a news event's weight halves every 48h


@dataclass(frozen=True)
class NewsEvidenceItem:
    news_event_id: int
    title: str
    source: str
    sentiment: float  # -1..+1
    matched_terms: tuple[str, ...]
    trust: float
    recency_weight: float  # 0..1
    confidence: float  # from news_market_links.confidence (topical match confidence)
    combined_weight: float  # trust * recency_weight * confidence


def score_sentiment(text: str) -> tuple[float, tuple[str, ...]]:
    """Pure function — returns (score in [-1, 1], matched terms)."""
    lowered = text.lower()
    hits_pos = [t for t in _POSITIVE_TERMS if t in lowered]
    hits_neg = [t for t in _NEGATIVE_TERMS if t in lowered]
    total = len(hits_pos) + len(hits_neg)
    if total == 0:
        return 0.0, ()
    score = (len(hits_pos) - len(hits_neg)) / total
    return round(score, 4), tuple(hits_pos + hits_neg)


def _trust_for_source(source: str) -> float:
    lowered = source.lower().strip()
    return _SOURCE_TRUST.get(lowered, 0.5)


def _recency_weight(published_at: str | None, now: datetime) -> float:
    if not published_at:
        return 0.3  # unknown publish time — treat cautiously, not zero
    try:
        published = datetime.fromisoformat(published_at)
    except ValueError:
        return 0.3
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    hours_ago = max(0.0, (now - published).total_seconds() / 3600)
    return round(0.5 ** (hours_ago / RECENCY_HALF_LIFE_HOURS), 4)


def collect_news_evidence(
    conn: sqlite3.Connection, provider: str, provider_market_id: str, now: datetime | None = None
) -> list[NewsEvidenceItem]:
    now = now or datetime.now(UTC)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "news_market_links" not in tables or "news_events" not in tables:
        return []
    rows = conn.execute(
        """
        SELECT ne.id, ne.title, ne.source, ne.published_at, nml.confidence
        FROM news_market_links nml
        JOIN news_events ne ON ne.id = nml.news_event_id
        WHERE nml.provider = ? AND nml.provider_market_id = ?
        ORDER BY ne.published_at DESC
        """,
        (provider, provider_market_id),
    ).fetchall()

    items: list[NewsEvidenceItem] = []
    for event_id, title, source, published_at, link_confidence in rows:
        sentiment, matched = score_sentiment(title)
        trust = _trust_for_source(source)
        recency = _recency_weight(published_at, now)
        combined = round(trust * recency * link_confidence, 4)
        items.append(
            NewsEvidenceItem(
                news_event_id=event_id, title=title, source=source, sentiment=sentiment,
                matched_terms=matched, trust=trust, recency_weight=recency,
                confidence=link_confidence, combined_weight=combined,
            )
        )
    return items


def compute_news_estimate(
    evidence: list[NewsEvidenceItem], market_yes_price: float | None
) -> tuple[SubmodelEstimate, float | None, int]:
    """Aggregates evidence into a weighted-average sentiment (-1..+1),
    turns it into a probability nudge around the market price (capped, same
    philosophy as the momentum submodel), and reports how many
    *independent* sources (distinct `source` values) confirm a
    non-neutral sentiment in the same direction — a simple, auditable
    stand-in for "independent confirmations".

    Returns (estimate, weighted_sentiment, confirmation_count).
    """
    scored = [e for e in evidence if e.matched_terms]
    if not scored or market_yes_price is None:
        return (
            SubmodelEstimate(
                name="news", estimated_yes_probability=None, weight=0.0, available=False,
                detail="Keine auswertbaren, verknüpften Nachrichten mit erkennbarer Tonalität gefunden.",
            ),
            None, 0,
        )

    total_weight = sum(e.combined_weight for e in scored)
    if total_weight <= 0:
        return (
            SubmodelEstimate(
                name="news", estimated_yes_probability=None, weight=0.0, available=False,
                detail="Nachrichten gefunden, aber Quellvertrauen/Aktualität zu gering für ein Signal.",
            ),
            None, 0,
        )

    weighted_sentiment = sum(e.sentiment * e.combined_weight for e in scored) / total_weight
    weighted_sentiment = round(weighted_sentiment, 4)

    positive_sources = {e.source for e in scored if e.sentiment > 0.1}
    negative_sources = {e.source for e in scored if e.sentiment < -0.1}
    confirmation_count = max(len(positive_sources), len(negative_sources))

    adjustment = max(-0.08, min(0.08, weighted_sentiment * 0.08))
    estimate = max(0.0, min(1.0, market_yes_price + adjustment))

    ensemble_weight = min(0.35, 0.05 * confirmation_count + total_weight * 0.05)

    return (
        SubmodelEstimate(
            name="news", estimated_yes_probability=round(estimate, 4), weight=ensemble_weight, available=True,
            detail=(
                f"{len(scored)} verknüpfte Nachricht(en) mit Tonalität ausgewertet, "
                f"gewichtete Stimmung {weighted_sentiment:+.2f} (-1=negativ, +1=positiv), "
                f"{confirmation_count} unabhängige bestätigende Quelle(n)."
            ),
        ),
        weighted_sentiment, confirmation_count,
    )
