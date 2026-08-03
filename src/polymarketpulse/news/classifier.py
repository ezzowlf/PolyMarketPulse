from __future__ import annotations

import re

from .base import NewsEvent

_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß0-9]+")
_STOPWORDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "for",
    "and",
    "is",
    "will",
    "der",
    "die",
    "das",
    "und",
    "im",
    "für",
    "zu",
}


def extract_entities(event: NewsEvent) -> tuple[str, ...]:
    """Very small, dependency-free keyword extractor: lowercased significant
    words from the title, longest-first. Deliberately simple and
    conservative — this is a matching aid, not an NLP claim, and it never
    invents relationships that aren't backed by shared terms."""
    words = [w.lower() for w in _WORD_RE.findall(event.title)]
    significant = [w for w in words if len(w) > 3 and w not in _STOPWORDS]
    # dedupe while preserving order
    seen: set[str] = set()
    entities: list[str] = []
    for word in significant:
        if word not in seen:
            seen.add(word)
            entities.append(word)
    return tuple(entities)
