from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Market

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def text_similarity(a: str, b: str) -> float:
    """Jaccard similarity over word sets — simple, explainable, no ML model
    or external service required."""
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


def date_similarity(market_a: Market, market_b: Market) -> float | None:
    if market_a.end_at is None or market_b.end_at is None:
        return None
    delta_days = abs((market_a.end_at - market_b.end_at).total_seconds()) / 86400
    return max(0.0, 1.0 - min(delta_days, 30) / 30)


def outcome_structure_match(market_a: Market, market_b: Market) -> bool | None:
    if not market_a.outcomes or not market_b.outcomes:
        return None
    return len(market_a.outcomes) == len(market_b.outcomes)


def category_match(market_a: Market, market_b: Market) -> bool | None:
    if not market_a.category or not market_b.category:
        return None
    return market_a.category.strip().lower() == market_b.category.strip().lower()


@dataclass(frozen=True)
class MarketMatchCandidate:
    market_a: Market
    market_b: Market
    text_similarity: float
    date_similarity: float | None
    outcome_structure_match: bool | None
    category_match: bool | None
    status: str = "candidate"

    @property
    def combined_score(self) -> float:
        parts = [self.text_similarity]
        if self.date_similarity is not None:
            parts.append(self.date_similarity)
        return sum(parts) / len(parts)


def find_candidate_matches(
    markets_a: list[Market], markets_b: list[Market], min_text_similarity: float = 0.35
) -> list[MarketMatchCandidate]:
    """Find plausible same-question markets across two providers. Returns
    `status='candidate'` only — nothing here is ever auto-confirmed. A
    human (or a separately reviewed process) must promote a candidate to
    'confirmed' before it is used for any cross-provider price comparison.
    """
    candidates: list[MarketMatchCandidate] = []
    for market_a in markets_a:
        for market_b in markets_b:
            similarity = text_similarity(market_a.question, market_b.question)
            if similarity < min_text_similarity:
                continue
            candidates.append(
                MarketMatchCandidate(
                    market_a=market_a,
                    market_b=market_b,
                    text_similarity=round(similarity, 3),
                    date_similarity=date_similarity(market_a, market_b),
                    outcome_structure_match=outcome_structure_match(market_a, market_b),
                    category_match=category_match(market_a, market_b),
                )
            )
    candidates.sort(key=lambda c: c.combined_score, reverse=True)
    return candidates


@dataclass(frozen=True)
class PriceDivergence:
    """Pure observation of a price gap between two markets — never a
    trading instruction. Whether the gap is meaningful depends on whether
    the two markets are actually confirmed as the same question."""

    market_a: Market
    market_b: Market
    yes_price_a: float | None
    yes_price_b: float | None
    divergence: float | None
    status: str


def compute_divergence(candidate: MarketMatchCandidate) -> PriceDivergence:
    a, b = candidate.market_a.yes_price, candidate.market_b.yes_price
    divergence = abs(a - b) if a is not None and b is not None else None
    return PriceDivergence(
        market_a=candidate.market_a,
        market_b=candidate.market_b,
        yes_price_a=a,
        yes_price_b=b,
        divergence=divergence,
        status=candidate.status,
    )
