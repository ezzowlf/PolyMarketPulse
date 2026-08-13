"""User-facing, persisted research-source availability.

This deliberately reads the real ``research_runs`` audit trail.  It never
turns a transport failure into an empty-result claim and it never invents a
provider that was not actually configured for the market's route.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta


def classify_research_status(
    *,
    published_forecast_probability: float | None,
    model_hypothesis_probability: float | None,
    forecast_status: str | None,
    has_research_run: bool,
) -> str:
    """Classify the user-facing state without treating an unpublished model as a forecast.

    This deliberately uses only persisted facts available to list views.  The
    detail endpoint refines it with live source availability, where a provider
    outage is known.  Keeping the classification here prevents each UI screen
    from inventing a different meaning for an absent published forecast.
    """
    if published_forecast_probability is not None:
        return "PUBLISHED"
    if forecast_status == "FORECAST_SUPPRESSED":
        return "FORECAST_BLOCKED"
    if model_hypothesis_probability is not None:
        return "MODEL_ONLY"
    if has_research_run:
        return "RESEARCHED_NO_EVIDENCE"
    return "NOT_RESEARCHED"


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def source_availability(storage, market_id: str) -> dict:
    """Return one compact status for the normal market UI plus audit facts.

    ``GDELT`` is a discovery source, therefore its sole failure is an
    informational warning unless the route has no successful required source.
    The data itself remains unavailable; severity controls presentation only.
    """
    market = storage.connection.execute(
        "SELECT provider_market_id FROM markets WHERE market_id = ?", (market_id,)
    ).fetchone()
    if market is None:
        return {"status": "UNKNOWN", "severity": "neutral", "provider_attempts": []}
    runs = storage.get_research_runs(provider_market_id=market[0], limit=30)
    if not runs:
        return {
            "status": "WEAK_DATA", "severity": "neutral", "provider_attempts": [],
            "message": "Für diesen Markt wurde noch keine gezielte Recherche ausgeführt.",
        }

    latest = runs[0]
    try:
        detail = json.loads(latest.get("detail_json") or "{}")
    except (TypeError, ValueError):
        detail = {}
    gdelt_status = detail.get("source_fetch_status")
    attempts = detail.get("source_attempts") or [{
        "provider": "gdelt", "role": "discovery", "status": gdelt_status or "UNKNOWN",
        "reason": "historischer Research-Lauf ohne vollständige Routing-Metadaten",
    }]
    failed = [a for a in attempts if a.get("status") == "SOURCE_FETCH_FAILED"]
    last_success = next(
        (r for r in runs if _safe_detail(r.get("detail_json")).get("source_fetch_status") == "OK"), None
    )
    last_failure = next(
        (r for r in runs if _safe_detail(r.get("detail_json")).get("source_fetch_status") == "SOURCE_FETCH_FAILED"), None
    )
    failures = 0
    for run in runs:
        if _safe_detail(run.get("detail_json")).get("source_fetch_status") == "SOURCE_FETCH_FAILED":
            failures += 1
        else:
            break
    next_check = _parse(latest.get("run_at"))
    if next_check and failed:
        next_check += timedelta(hours=min(6 * (2 ** failures), 24 * 14))

    if failed:
        # Discovery is useful for finding additional evidence, but a failed
        # discovery query must not eclipse a successful, route-specific
        # primary input such as GovTrack or IMF PortWatch.  The normal UI
        # needs to distinguish a degraded discovery layer from an actually
        # unavailable forecast-critical source.
        primary_succeeded = any(
            attempt.get("role") == "primary" and attempt.get("status") == "OK"
            for attempt in attempts
        )
        discovery_only_failure = all(a.get("role") == "discovery" for a in failed)
        if discovery_only_failure and primary_succeeded:
            return {
                "status": "DISCOVERY_DEGRADED", "severity": "info",
                "title": "ZusÃ¤tzliche Discovery-Quelle derzeit nicht erreichbar",
                "message": "Die primÃ¤re, marktspezifische Datenquelle wurde erfolgreich verarbeitet. Nur die optionale Discovery-Suche ist derzeit nicht erreichbar.",
                "provider_attempts": attempts, "last_successful_fetch": latest.get("run_at"),
                "last_failed_fetch": last_failure and last_failure.get("run_at"), "consecutive_failures": failures,
                "retry_status": "BACKOFF" if failures else "SCHEDULED", "next_check_at": next_check and next_check.isoformat(),
                "alternative_providers": detail.get("alternative_providers", []),
            }
        return {
            "status": "SOURCE_UNREACHABLE", "severity": "info" if all(a.get("role") == "discovery" for a in failed) else "warning",
            "title": "Datenquelle derzeit nicht erreichbar",
            "message": "Eine für diese Analyse vorgesehene Quelle konnte aktuell nicht abgerufen werden. PolyMarketPulse hat deshalb keine fehlenden Informationen erfunden und die Prognose aufgrund dieses Abruffehlers nicht verändert.",
            "provider_attempts": attempts, "last_successful_fetch": last_success and last_success.get("run_at"),
            "last_failed_fetch": last_failure and last_failure.get("run_at"), "consecutive_failures": failures,
            "retry_status": "BACKOFF" if failures else "SCHEDULED", "next_check_at": next_check and next_check.isoformat(),
            "alternative_providers": detail.get("alternative_providers", []),
        }
    if gdelt_status == "OK" and latest.get("sources_accepted", 0) == 0:
        return {
            "status": "NO_RELEVANT_EVIDENCE", "severity": "neutral", "title": "Keine relevante Evidenz",
            "message": "Die vorgesehenen Quellen wurden erfolgreich abgefragt, lieferten aber keine relevante, unabhängige Evidenz.",
            "provider_attempts": attempts, "last_successful_fetch": latest.get("run_at"),
            "alternative_providers": detail.get("alternative_providers", []),
        }
    return {
        "status": "SUFFICIENT_DATA", "severity": "positive", "title": "Ausreichende Daten",
        "message": "Die vorhandenen Quellen konnten für diese Analyse erfolgreich verarbeitet werden.",
        "provider_attempts": attempts, "last_successful_fetch": latest.get("run_at"),
        "alternative_providers": detail.get("alternative_providers", []),
    }


def _safe_detail(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
