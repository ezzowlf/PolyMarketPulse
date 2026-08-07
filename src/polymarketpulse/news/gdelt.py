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


def build_query_for_question(question: str, extra_terms: tuple[str, ...] = (), max_terms: int = 6) -> str:
    """Builds a GDELT search query from a market question's significant
    words (reuses the same simple stopword-free heuristic as
    news/classifier.py's extract_entities, applied to the question text
    directly). ORs the terms so any of them can match a relevant article."""
    from .classifier import _STOPWORDS

    words = [w.strip(".,?!\"'()").lower() for w in question.split()]
    significant = [w for w in words if len(w) > 3 and w not in _STOPWORDS]
    terms = list(dict.fromkeys(significant))[:max_terms] + list(extra_terms)
    if not terms:
        return ""
    return " OR ".join(f'"{t}"' if " " in t else t for t in terms)
