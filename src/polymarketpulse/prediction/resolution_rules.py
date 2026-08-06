"""Resolution-Rule-Parser — heuristic, regex-based extraction of what would
make a market resolve YES vs. NO, plus the market's core subject terms.
Deliberately simple and auditable (no LLM, no NLP model): every extracted
phrase is a literal substring of the source text, so it's always possible to
trace a match back to the exact sentence it came from.

This is intentionally conservative. If no explicit "resolves YES/NO if ..."
pattern is found, the YES/NO term lists stay empty rather than guessing —
callers (evidence.py) treat that as "resolution rules unclear", not as
"resolves however we like"."""

from __future__ import annotations

import re

from ..news.classifier import _STOPWORDS, _WORD_RE

_YES_PATTERN = re.compile(
    r"resolves?\s+(?:to\s+)?[\"']?yes[\"']?\s+(?:if|when)\s+(.+?)(?:[.\n]|$)", re.IGNORECASE
)
_NO_PATTERN = re.compile(
    r"resolves?\s+(?:to\s+)?[\"']?no[\"']?\s+(?:if|when)\s+(.+?)(?:[.\n]|$)", re.IGNORECASE
)


def _significant_terms(text: str, max_terms: int = 12) -> tuple[str, ...]:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    significant = [w for w in words if len(w) > 3 and w not in _STOPWORDS]
    seen: set[str] = set()
    ordered: list[str] = []
    for word in significant:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return tuple(ordered[:max_terms])


def parse_resolution_conditions(
    question: str, resolution_text: str | None
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Returns (yes_trigger_terms, no_trigger_terms, subject_terms).

    yes_trigger_terms / no_trigger_terms: significant words pulled from the
    explicit "resolves YES/NO if ..." clause, if one is present in
    `resolution_text`. Empty tuple if no such clause was found.

    subject_terms: significant words from the market question itself — used
    to judge whether a news item is even about this market at all, distinct
    from whether it argues YES or NO.
    """
    subject_terms = _significant_terms(question)

    if not resolution_text:
        return (), (), subject_terms

    yes_terms: tuple[str, ...] = ()
    no_terms: tuple[str, ...] = ()

    yes_match = _YES_PATTERN.search(resolution_text)
    if yes_match:
        yes_terms = _significant_terms(yes_match.group(1))

    no_match = _NO_PATTERN.search(resolution_text)
    if no_match:
        no_terms = _significant_terms(no_match.group(1))

    return yes_terms, no_terms, subject_terms
