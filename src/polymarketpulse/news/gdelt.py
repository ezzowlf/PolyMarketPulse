"""GDELT DOC 2.1 API client — free, public, no API key required. Used as a
broad-coverage source of primary/OSINT-style reporting (official statements,
local media, wire coverage) beyond the small curated RSS feed list, per the
project's hard rule: only lawfully public information, never paid/private/
stolen/hacked sources. See `news/rss.py` for the same "no scraping-evasion,
no authentication" policy — this module follows it identically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url
from .base import NewsEvent

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def _parse_gdelt_date(value: str | None) -> datetime | None:
    # GDELT "seendate" format: YYYYMMDDTHHMMSSZ
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def fetch_gdelt(
    query: str, timespan: str = "3d", max_records: int = 25, timeout: float = 10.0
) -> list[NewsEvent]:
    """Query the free GDELT DOC 2.1 API for recent articles matching `query`.
    Never raises on network/parse failure — returns [] so a missing/rate-
    limited GDELT response never breaks a scan."""
    if not query.strip():
        return []
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(max_records),
        "timespan": timespan,
        "format": "json",
        "sort": "datedesc",
    }
    try:
        assert_safe_url(GDELT_DOC_URL)
    except SSRFError:
        return []

    try:
        response = httpx.get(
            GDELT_DOC_URL, params=params, timeout=timeout,
            headers={"User-Agent": "PolymarketPulse/0.2"},
        )
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            return []
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []

    now = datetime.now(UTC)
    events: list[NewsEvent] = []
    for article in payload.get("articles", []):
        title = (article.get("title") or "").strip()
        url = (article.get("url") or "").strip()
        if not title or not url:
            continue
        domain = article.get("domain") or urlparse(url).netloc
        tone_raw = article.get("tone")
        tone = None
        if tone_raw is not None:
            try:
                tone = float(tone_raw)
            except (TypeError, ValueError):
                tone = None
        events.append(
            NewsEvent(
                source=f"gdelt:{domain}",
                source_url=url,
                title=title,
                published_at=_parse_gdelt_date(article.get("seendate")),
                fetched_at=now,
                tone=tone,
                source_domain=domain,
            )
        )
    return events


# Real function-word list (not just a length filter). These are words that
# regularly slip through news/classifier.py's `_STOPWORDS` (which only
# excludes a handful of length<=3 words) because they're length>3 but still
# non-substantive for search purposes — auxiliary verbs, temporal/logical
# connectors, generic market-question boilerplate. Deliberately broad but
# reviewed by hand; extend by adding words, no retraining involved.
_FUNCTION_WORDS = {
    "before", "after", "next", "state", "will", "does", "shall",
    "would", "could", "should", "than", "then", "when", "while", "during",
    "until", "since", "about", "above", "below", "between", "under", "over",
    "into", "onto", "from", "with", "without", "within", "which", "what",
    "where", "there", "their", "they", "them", "this", "that", "these",
    "those", "have", "having", "been", "being", "were", "was", "are", "is",
    "did", "not", "any", "all", "each", "every", "some", "more",
    "most", "less", "least", "many", "much", "such", "only", "also", "even",
    "still", "just", "again", "once", "here", "market", "question", "resolve",
    "resolves", "resolution", "yes", "no", "occur", "occurs", "happen",
    "happens", "reach", "reaches", "end", "ends", "date", "year",
}


def build_query_for_question(question: str, extra_terms: tuple[str, ...] = (), max_terms: int = 6) -> str:
    """Builds a GDELT search query from a market question, phrase-quoting
    detected multi-word entities (e.g. "Strait of Hormuz") and filtering a
    real function-word list rather than a bare length check, so queries are
    meaningfully more specific than a flat keyword-OR list.

    Reuses `news/classifier.py`'s `_STOPWORDS` as the base filter, extended
    with `_FUNCTION_WORDS` above (real audit finding: "before"/"next"/
    "state" and similar length>3 non-substantive words were slipping
    through the old length<=3-only filter). Multi-word entity detection is
    a locally-scoped extension of the same "capitalized proper-noun run"
    idea already used by `prediction/semantics.py::_extract_actors` — that
    function alone doesn't handle connector words like "of"/"the" inside a
    phrase (it would split "Strait of Hormuz" into two disconnected single
    words "Strait"/"Hormuz"), so `_extract_phrase_entities` below extends
    the same approach to tolerate a small connector-word list between
    capitalized words, rather than building a new NER system."""
    from .classifier import _STOPWORDS

    phrases = _extract_phrase_entities(question)
    phrase_words: set[str] = set()
    for phrase in phrases:
        phrase_words.update(w.lower() for w in phrase.split())

    words = [w.strip(".,?!\"'()").lower() for w in question.split()]
    significant = [
        w for w in words
        if len(w) > 3 and w not in _STOPWORDS and w not in _FUNCTION_WORDS and w not in phrase_words
    ]
    single_terms = list(dict.fromkeys(significant))

    # Phrases first (higher specificity), then leftover significant single
    # words, capped at max_terms total so the query doesn't balloon.
    terms = list(dict.fromkeys(phrases)) + single_terms
    terms = terms[:max_terms] + list(extra_terms)
    if not terms:
        return ""
    return " OR ".join(f'"{t}"' if " " in t else t for t in terms)


# Connector words tolerated *inside* a multi-word proper-noun phrase (e.g.
# "Strait of Hormuz", "United Arab Emirates" needs no connector, "Bank of
# England" does). Deliberately small — this is a matching aid, not a claim
# of full NER, and a phrase is only ever kept if it starts and ends on a
# real capitalized word.
_PHRASE_CONNECTORS = {"of", "the", "for", "de", "la", "al", "bin", "van", "der", "and"}
# Sentence-initial capitalization false positives (mirrors
# `semantics.py::_extract_actors`'s own exclusion list) — stripped from the
# front of a candidate phrase, never treated as part of an entity name.
_PHRASE_LEADING_EXCLUDE = {"the", "a", "an", "will", "this", "is", "does", "after", "before", "when"}


def _extract_phrase_entities(text: str) -> tuple[str, ...]:
    """Extracts multi-word capitalized entity phrases from free text,
    tolerating a small set of lowercase connector words inside the phrase
    (see `_PHRASE_CONNECTORS`). Only ever returns phrases of 2+ real
    (non-connector) words — never invents a relationship that isn't backed
    by consecutive capitalization in the source text."""
    raw_tokens = text.replace("?", " ").replace(",", " ").split()
    tokens = [t.strip(".!\"'()") for t in raw_tokens]

    phrases: list[str] = []
    current: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        lowered = tok.lower()
        if (tok[0].isupper() and lowered not in _PHRASE_CONNECTORS) or (
            lowered in _PHRASE_CONNECTORS and current
        ):
            current.append(tok)
        else:
            _flush_phrase(current, phrases)
            current = []
    _flush_phrase(current, phrases)

    # Drop a sentence-initial false-positive leading word (e.g. "Will
    # Trump" -> "Trump" alone, which then fails the 2+ word requirement and
    # is correctly dropped entirely rather than phrase-quoted).
    cleaned: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        words = phrase.split()
        while words and words[0].lower() in _PHRASE_LEADING_EXCLUDE:
            words = words[1:]
        while words and words[-1].lower() in _PHRASE_CONNECTORS:
            words = words[:-1]
        if len(words) < 2:
            continue
        cleaned_phrase = " ".join(words)
        if cleaned_phrase not in seen:
            seen.add(cleaned_phrase)
            cleaned.append(cleaned_phrase)
    return tuple(cleaned)


def _flush_phrase(current: list[str], phrases: list[str]) -> None:
    # Trim a trailing connector that was never followed by another
    # capitalized word (e.g. "...normal by August" where "by" isn't even a
    # connector — this guards the case where the sequence ends mid-connector).
    while current and current[-1].lower() in _PHRASE_CONNECTORS:
        current = current[:-1]
    if len(current) >= 2:
        phrases.append(" ".join(current))
