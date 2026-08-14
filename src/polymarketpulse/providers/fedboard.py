"""Official Federal Reserve Board policy-decision input.

The live Fed shadow model has one validated feature: the previous scheduled
FOMC action.  This module obtains that feature from the Board's own calendar
and linked statement, retaining both the normalized action and its raw URL.
It deliberately returns ``None`` on an uncertain statement rather than
guessing from market prices or an effective-rate proxy.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url, get_ssl_context

FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


@dataclass(frozen=True)
class FedPolicyDecision:
    action: str
    decision_date: date
    target_lower: float | None
    target_upper: float | None
    source_url: str
    retrieved_at: str

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "decision_date": self.decision_date.isoformat(),
            "target_range": [self.target_lower, self.target_upper],
            "provider": "federal_reserve_board",
            "raw_source_url": self.source_url,
            "retrieved_at": self.retrieved_at,
        }


def _get(url: str, timeout: float) -> str | None:
    try:
        assert_safe_url(url)
        response = httpx.get(url, timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"}, verify=get_ssl_context())
        response.raise_for_status()
    except (httpx.HTTPError, SSRFError):
        return None
    if len(response.content) > MAX_RESPONSE_BYTES:
        return None
    return response.text


def _statement_urls(calendar_html: str, today: date) -> list[tuple[date, str]]:
    urls: list[tuple[date, str]] = []
    for raw in re.findall(r'''href=["']([^"']*monetary(20\d{6})a\.htm[^"']*)["']''', calendar_html, re.IGNORECASE):
        href, compact_date = raw
        try:
            decision_date = date(int(compact_date[:4]), int(compact_date[4:6]), int(compact_date[6:]))
        except ValueError:
            continue
        if decision_date <= today:
            if href.startswith("http"):
                url = href
            else:
                url = "https://www.federalreserve.gov/" + href.lstrip("./")
            urls.append((decision_date, url))
    return urls


def _number(value: str) -> float | None:
    value = value.strip().replace(" ", "")
    match = re.fullmatch(r"(\d+)(?:-(\d+)/(\d+))?", value)
    if not match:
        return None
    whole, numerator, denominator = match.groups()
    return float(whole) + (float(numerator) / float(denominator) if numerator else 0.0)


def _parse_decision(statement_html: str, decision_date: date, source_url: str, retrieved_at: str) -> FedPolicyDecision | None:
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", statement_html))).lower()
    if "maintain the target range" in text:
        action = "UNCHANGED"
    elif re.search(r"(?:raise|increase) the target range.{0,100}(?:1/4|25 basis)", text):
        action = "HIKE_25"
    elif re.search(r"(?:raise|increase) the target range.{0,100}(?:1/2|50 basis)", text):
        action = "HIKE_50_PLUS"
    elif re.search(r"(?:lower|decrease) the target range.{0,100}(?:1/4|25 basis)", text):
        action = "CUT_25"
    elif re.search(r"(?:lower|decrease) the target range.{0,100}(?:1/2|50 basis)", text):
        action = "CUT_50_PLUS"
    else:
        return None
    target = re.search(r"target range for the federal funds rate at\s+([0-9]+(?:-[0-9]+/[0-9]+)?)\s+to\s+([0-9]+(?:-[0-9]+/[0-9]+)?)\s+percent", text)
    lower, upper = (_number(target.group(1)), _number(target.group(2))) if target else (None, None)
    return FedPolicyDecision(action, decision_date, lower, upper, source_url, retrieved_at)


def fetch_latest_policy_decision(timeout: float = 10.0, *, today: date | None = None) -> FedPolicyDecision | None:
    """Fetch the latest dated official FOMC statement no later than today."""
    reference = today or datetime.now(UTC).date()
    calendar_html = _get(FOMC_CALENDAR_URL, timeout)
    if calendar_html is None:
        return None
    candidates = _statement_urls(calendar_html, reference)
    if not candidates:
        return None
    decision_date, source_url = max(candidates)
    statement_html = _get(source_url, timeout)
    if statement_html is None:
        return None
    return _parse_decision(statement_html, decision_date, source_url, datetime.now(UTC).isoformat())
