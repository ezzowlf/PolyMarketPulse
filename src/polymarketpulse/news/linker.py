from __future__ import annotations

from dataclasses import dataclass

from ..models import Market
from .base import NewsEvent
from .classifier import extract_entities

# min_confidence tuning decision (audit Part 3): raised from 0.15 to 0.2.
# Reasoning, documented rather than rubber-stamped: `link_news_to_markets`'s
# own confidence formula (shared-term-count / entity-set-size) is unchanged
# by the Part 1 GDELT query-specificity fix (that fix only narrows what
# gets *fetched*, not how an already-fetched article's title is matched
# against a market question here). The real, verified example from the
# audit — `polymarket:3231771` (Trump market) — had 12 raw links at 0.15
# but 0 that survived `classify_evidence_relation`'s scoring gate, meaning
# the raw-count gate (`MIN_EVIDENCE_ITEMS_FOR_ESTIMATE`) was satisfied by
# noise. Raising the floor only *some* (not e.g. to 0.4+) is deliberate:
# this formula has no phrase-awareness of its own, so a genuinely relevant
# 2-shared-term article on a short market question can legitimately sit
# just above 0.15-0.2; going much higher risked cutting real matches
# without fixing the underlying formula (out of scope here — the scoring
# gate downstream, deliberately left untouched, is what should reject
# off-topic links, not an aggressively high raw-linking floor). 0.2 trims
# the weakest single-shared-word coincidental matches while leaving
# 2+-shared-term matches intact.
DEFAULT_MIN_CONFIDENCE = 0.2


@dataclass(frozen=True)
class NewsMarketLink:
    news_event: NewsEvent
    market: Market
    match_reason: str
    matched_terms: tuple[str, ...]
    confidence: float
    confirmed: str = "automatic"


def link_news_to_markets(
    events: list[NewsEvent], markets: list[Market], min_confidence: float = 0.2
) -> list[NewsMarketLink]:
    """Term-overlap based linking. Confidence is the fraction of the news
    event's extracted terms that also appear in the market question — a
    deliberately simple, auditable signal. Never fabricates a link: markets
    with zero shared terms are never returned."""
    links: list[NewsMarketLink] = []
    for event in events:
        entities = extract_entities(event)
        if not entities:
            continue
        entity_set = set(entities)
        for market in markets:
            question_words = {w.lower() for w in market.question.split()}
            matched = entity_set & question_words
            if not matched:
                continue
            confidence = len(matched) / len(entity_set)
            if confidence < min_confidence:
                continue
            links.append(
                NewsMarketLink(
                    news_event=event,
                    market=market,
                    match_reason="shared_terms",
                    matched_terms=tuple(sorted(matched)),
                    confidence=round(confidence, 3),
                )
            )
    return links
