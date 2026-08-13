"""IMF PortWatch chokepoint transit-data provider — free, keyless, official
IMF data source, used as the real, resolution-question-specific second
source for strategic-waterway markets (e.g. Hormuz).

Root cause this addresses: Hormuz-shaped markets (`polymarket:2774056` and
siblings) each resolve on a specific, published, official threshold — e.g.
"IMF PortWatch 7-day moving average of transit calls for the Strait of
Hormuz >= 60" — but the only linked evidence for these markets has always
been generic news articles about "disruption" or "tension" in the region,
never the actual quantitative resolution data itself. A generic second
GDELT/RSS article about Iran/Hormuz would NOT actually confirm or refute
the resolution question; this module fetches the real underlying dataset
PortWatch's own dashboard uses.

Verified live 2026-08-13: IMF PortWatch's dashboard at
`https://portwatch.imf.org/pages/cb5856222a5b4105adc6ee7e880a1730` is an
Esri ArcGIS Hub page backed by a real, public, keyless ArcGIS Feature
Service (`Daily_Chokepoints_Data`, discovered via ArcGIS's own public
search API, itemid `3da2b9ca97684916b75c4013f95d18ab`). Queried directly
for `portname='Strait of Hormuz'`: real daily transit-call counts
(`n_total`) for 2026-07-31 through 2026-08-09 ranged 1-8 per day -- an
order of magnitude below the market's 60-call threshold, and a real,
directly resolution-relevant data point no news article provides.

Failure mode: any network error, empty result, or unparseable response
returns None -- never a fabricated transit count."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url, get_ssl_context

_QUERY_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)

# Real PortWatch chokepoint names this module can look up (extend as
# needed -- these are the exact `portname` values PortWatch's own dataset
# uses, discovered live via the ArcGIS query above).
CHOKEPOINT_STRAIT_OF_HORMUZ = "Strait of Hormuz"
CHOKEPOINT_BAB_EL_MANDEB = "Bab-el-Mandeb"


@dataclass(frozen=True)
class ChokepointTransitData:
    """Real daily transit-call observations for one chokepoint, plus the
    real 7-day moving average PortWatch itself uses for resolution
    (matches the exact metric most of these markets' resolution rules
    cite)."""

    chokepoint: str
    observations: tuple[tuple[date, int], ...]  # (date, n_total), most recent last
    seven_day_average: float | None
    fetched_at: datetime

    def as_dict(self) -> dict:
        return {
            "chokepoint": self.chokepoint,
            "observations": [(d.isoformat(), n) for d, n in self.observations],
            "seven_day_average": self.seven_day_average,
            "fetched_at": self.fetched_at.isoformat(),
        }


def fetch_chokepoint_transit_data(
    chokepoint: str, days: int = 10, timeout: float = 15.0,
) -> ChokepointTransitData | None:
    """Fetches the real, most recent daily transit-call counts for one
    named chokepoint and computes the real trailing 7-day average."""
    where = f"portname='{chokepoint}'"
    try:
        assert_safe_url(_QUERY_URL)
    except SSRFError:
        return None

    try:
        response = httpx.get(
            _QUERY_URL,
            params={
                "where": where, "outFields": "date,n_total",
                "orderByFields": "date DESC", "resultRecordCount": str(days),
                "f": "json",
            },
            timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"},
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

    features = payload.get("features") or []
    observations: list[tuple[date, int]] = []
    for feature in features:
        attrs = feature.get("attributes", {})
        raw_date = attrs.get("date")
        n_total = attrs.get("n_total")
        if raw_date is None or n_total is None:
            continue
        try:
            if isinstance(raw_date, (int, float)):
                # Some ArcGIS deployments return epoch milliseconds.
                obs_date = datetime.fromtimestamp(raw_date / 1000, tz=UTC).date()
            else:
                obs_date = date.fromisoformat(str(raw_date)[:10])
            observations.append((obs_date, int(n_total)))
        except (TypeError, ValueError, OSError):
            continue

    if not observations:
        return None
    observations.sort(key=lambda pair: pair[0])

    last_seven = observations[-7:]
    seven_day_average = (
        sum(n for _, n in last_seven) / len(last_seven) if last_seven else None
    )

    return ChokepointTransitData(
        chokepoint=chokepoint, observations=tuple(observations),
        seven_day_average=round(seven_day_average, 2) if seven_day_average is not None else None,
        fetched_at=datetime.now(UTC),
    )
