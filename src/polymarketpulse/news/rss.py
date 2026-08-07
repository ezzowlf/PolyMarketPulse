from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url
from .base import NewsEvent

# Only free, official, publicly accessible feeds. No paywalled content, no
# scraping-evasion, no authentication. Users can extend this list in their
# own config; nothing here requires credentials.
DEFAULT_FEEDS: dict[str, str] = {
    "federal_reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "ecb": "https://www.ecb.europa.eu/rss/press.html",
    # Verified live 2026-08 (the old "/feed/" URL now 404s): whitehouse.gov
    # restructured its RSS paths under /news/.
    "whitehouse": "https://www.whitehouse.gov/news/feed/",
    "un_news": "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
    "state_department": "https://www.state.gov/rss-feed/press-releases/feed/",
    "sec": "https://www.sec.gov/news/pressreleases.rss",
    # NATO's RSS feed (previously "nato": ".../news.rss") could not be
    # relocated as of 2026-08 — every URL pattern tried 404s and no current
    # feed link could be found on nato.int's own news pages. Removed rather
    # than left pointing at a guaranteed-dead endpoint; re-add once a real
    # current feed URL is confirmed (see news-fetch's per-feed error
    # handling, which already tolerates a dead entry gracefully in the
    # meantime — this is about not shipping a known-broken default, not a
    # crash risk).
}


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def fetch_feed(url: str, source_name: str, timeout: float = 20.0) -> list[NewsEvent]:
    """Fetch and parse one RSS/Atom feed. Never raises on malformed XML or
    missing fields — returns whatever items could be parsed, empty list on
    total failure."""
    try:
        assert_safe_url(url)
    except SSRFError:
        return []

    try:
        response = httpx.get(url, timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"})
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    if len(response.content) > MAX_RESPONSE_BYTES:
        return []

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return []

    now = datetime.now(UTC)
    events: list[NewsEvent] = []

    # RSS 2.0: <rss><channel><item>...
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        events.append(
            NewsEvent(
                source=source_name,
                source_url=link,
                title=title,
                published_at=_parse_date(item.findtext("pubDate")),
                fetched_at=now,
                summary=(item.findtext("description") or "").strip()[:500],
                source_domain=urlparse(link).netloc,
            )
        )

    # Atom: <feed><entry>...
    if not events:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "") if link_el is not None else ""
            if not title or not link:
                continue
            events.append(
                NewsEvent(
                    source=source_name,
                    source_url=link,
                    title=title,
                    published_at=_parse_date(entry.findtext("atom:updated", namespaces=ns)),
                    fetched_at=now,
                    source_domain=urlparse(link).netloc,
                )
            )

    return events


def fetch_all(feeds: dict[str, str] | None = None, timeout: float = 20.0) -> list[NewsEvent]:
    feeds = feeds or DEFAULT_FEEDS
    events: list[NewsEvent] = []
    for name, url in feeds.items():
        events.extend(fetch_feed(url, name, timeout=timeout))
    return events
