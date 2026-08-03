from __future__ import annotations

from dataclasses import dataclass

from ..models import Market
from .base import NewsEvent
from .classifier import extract_entities


@dataclass(frozen=True)
class NewsMarketLink:
    news_event: NewsEvent
    market: Market
    match_reason: str
    matched_terms: tuple[str, ...]
    confidence: float
    confirmed: str = "automatic"


def link_news_to_markets(
    events: list[NewsEvent], markets: list[Market], min_confidence: float = 0.15
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
