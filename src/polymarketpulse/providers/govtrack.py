"""GovTrack.us bill-status provider — free, keyless, public REST API.

Real-data-integration slice (Clarity Act, `polymarket:1163699`, H.R.3633):
`source_registry.py` already correctly RECOMMENDS `congress_gov`/`senate_gov`/
`house_gov` for `legislation`-typed markets, but none of those three actually
has a real, free, keyless fetch mechanism reachable from this codebase,
verified live from this sandbox:

  - `https://www.congress.gov/bill/119th-congress/house-bill/3633` -> live
    HTTP 403 (bot-detection block on the human-facing site).
  - `https://api.congress.gov/v3/bill/119/hr/3633` -> live HTTP 403,
    `API_KEY_MISSING` — congress.gov's real structured API requires a free,
    but still separately issued, API key this project does not have (see
    HANDOFF.md's final report for the request to the project owner).
  - `house.gov`/`senate.gov` do not publish a per-bill structured status
    API or a per-bill RSS feed at all — only general press-release feeds
    unrelated to a specific bill's status.

GovTrack.us's public API (`https://www.govtrack.us/api/v2/bill`) is a real,
free, keyless, structured JSON endpoint that mirrors congress.gov's own
official bill-status data (it ingests the same government bulk-data feeds
Congress.gov itself publishes) without requiring an API key or scraping the
bot-protected congress.gov site. Verified live 2026-08-12: HTTP 200, real
JSON, `current_status`/`current_status_date`/`major_actions` fields for
H.R.3633 (119th Congress) exactly matching the bill's real, current
legislative status (passed House 2025-07-17, currently before the Senate).

Because GovTrack is a third-party aggregator (not itself a `.gov` domain),
it is registered in `source_registry.py` as `SECONDARY_REPUTABLE`, not
`PRIMARY_OFFICIAL` — an honest trust label, not a claim of higher
verification than this integration actually performs.

Failure mode: any network error, non-200 response, or unparseable JSON
returns None — callers must treat that as "data unavailable" and must NOT
fabricate a fallback bill status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url, get_ssl_context

_API_BASE_URL = "https://www.govtrack.us/api/v2/bill"

# GovTrack's bill_type query values -> our own house_bill/senate_bill etc.
# aliases (kept as a real, tiny lookup table so callers don't need to know
# GovTrack's specific vocabulary).
BILL_TYPE_HOUSE_BILL = "house_bill"
BILL_TYPE_SENATE_BILL = "senate_bill"


@dataclass(frozen=True)
class BillAction:
    """One real, dated entry from GovTrack's `major_actions` list — a
    government-recorded legislative action (introduction, committee vote,
    floor vote, signature, veto, ...), not a news article about the bill."""

    occurred_at: date | None
    text: str


@dataclass(frozen=True)
class BillStatus:
    """Real, current legislative status for one bill, as reported by
    GovTrack's public API at fetch time."""

    congress: int
    bill_type: str
    number: int
    display_number: str  # e.g. "H.R. 3633"
    title: str | None
    current_status: str  # GovTrack's own status slug, e.g. "pass_over_house"
    current_status_label: str
    current_status_description: str
    current_status_date: date | None
    introduced_date: date | None
    is_alive: bool
    link: str  # GovTrack's own bill page — the real source URL for citation
    major_actions: tuple[BillAction, ...]
    fetched_at: datetime


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def fetch_bill_status(
    congress: int, bill_type: str, number: int, timeout: float = 15.0
) -> BillStatus | None:
    """Fetch one bill's real, current status from GovTrack's public API.

    `bill_type` must be a GovTrack bill_type value (see BILL_TYPE_* above).
    Returns None on any network/parse failure or if the bill isn't found —
    never a guessed/fabricated status.
    """
    url = f"{_API_BASE_URL}?congress={congress}&bill_type={bill_type}&number={number}"
    try:
        assert_safe_url(url)
    except SSRFError:
        return None

    try:
        response = httpx.get(
            url, timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"},
            verify=get_ssl_context(),
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    if len(response.content) > MAX_RESPONSE_BYTES:
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    objects = payload.get("objects") or []
    if not objects:
        return None
    obj = objects[0]

    major_actions: list[BillAction] = []
    for entry in obj.get("major_actions") or []:
        # GovTrack's major_actions entries are 4-tuples:
        # [datetime_repr_str, state_code, action_text, raw_xml]. We only use
        # the human-readable action_text and try to recover a date from the
        # datetime_repr_str (a Python repr string, e.g.
        # "datetime.datetime(2025, 7, 17, 15, 30, 31)") since GovTrack does
        # not expose a clean ISO field for this list.
        if not isinstance(entry, list) or len(entry) < 3:
            continue
        dt_repr, _state, text = entry[0], entry[1], entry[2]
        occurred_at = None
        try:
            import re as _re

            m = _re.search(r"datetime\.datetime\((\d+),\s*(\d+),\s*(\d+)", str(dt_repr))
            if m:
                occurred_at = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except (ValueError, TypeError):
            occurred_at = None
        major_actions.append(BillAction(occurred_at=occurred_at, text=str(text)))

    return BillStatus(
        congress=int(obj.get("congress")) if obj.get("congress") is not None else congress,
        bill_type=str(obj.get("bill_type") or bill_type),
        number=int(obj.get("number")) if obj.get("number") is not None else number,
        display_number=str(obj.get("display_number") or f"{bill_type} {number}"),
        title=obj.get("title"),
        current_status=str(obj.get("current_status") or "unknown"),
        current_status_label=str(obj.get("current_status_label") or ""),
        current_status_description=str(obj.get("current_status_description") or ""),
        current_status_date=_parse_date(obj.get("current_status_date")),
        introduced_date=_parse_date(obj.get("introduced_date")),
        is_alive=bool(obj.get("is_alive", True)),
        link=str(obj.get("link") or ""),
        major_actions=tuple(major_actions),
        fetched_at=datetime.now(UTC),
    )
