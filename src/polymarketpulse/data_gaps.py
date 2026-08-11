"""Data Gap Engine

Identifies missing information for a market and calculates which data would
reduce uncertainty most. Implements the "Data Gap Engine" principle from Phase O:

- Explicitly calculate missing information
- Prioritize data by impact on uncertainty reduction
- Track which sources would help most
- Report TOP DATA GAPS for explainability

This helps us know what to build next and prevents false confidence from
missing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

from .source_registry import recommend_sources_for_gap

if TYPE_CHECKING:
    from .prediction.world_state import ResolutionPath

# Data gap categories (spec: never claim data exists when it doesn't)
DataGapCategory = Literal[
    "NEWS_PRIMARY",      # No primary news evidence (Reuters, AP, official feeds)
    "NEWS_SECONDARY",    # No secondary news coverage
    "STRUCTURED_DATA",   # No structured data (shipping, sports, quant)
    "EVENT_GRAPH",       # No event relations stored
    "HISTORICAL_COMPARABLE",  # Weak/insufficient historical comparables
    "TIME_HORIZON",      # Unknown or incompatible time horizons
    "STATE_ENGINE",      # Missing current-world state
    "MARKET_HISTORY",    # Insufficient Polymarket price history
    "GEOGRAPHIC_DATA",   # Missing geographic context
    "ECONOMIC_DATA",     # Missing macro/economic indicators
    # Block D Part 3: a specific, named step of a real multi-step
    # resolution structure (Block C's ResolutionStep/ResolutionPath) whose
    # status is genuinely "unknown" — e.g. a legislation market's senate
    # vote never having a real dated evidence item to derive a status from.
    "RESOLUTION_PATH",
]

# Human-readable German step names for RESOLUTION_PATH gap descriptions —
# real, plain translations of the literal step-name strings
# world_state._MULTISTEP_STRUCTURE_BY_EVENT_TYPE already uses (currently
# only the "legislation" structure), never invented text.
_STEP_NAME_DE: dict[str, str] = {
    "introduced": "Einbringung des Gesetzentwurfs",
    "committee": "Ausschussphase",
    "house_vote": "Abstimmung im Repräsentantenhaus",
    "senate_vote": "Abstimmung im Senat",
    "presidential_action": "Unterzeichnung/Veto durch den Präsidenten",
}


class GapPriority(Enum):
    """Priority for addressing data gaps.
    
    HIGH: Critical for any meaningful forecast
    MEDIUM: Important for higher confidence
    LOW: Nice to have for refinement
    """
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class DataGap:
    """One identified data gap for a market."""
    category: DataGapCategory
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    description: str
    priority: GapPriority
    impact_on_confidence: float  # How much confidence would improve
    recommended_sources: tuple[str, ...]  # Which sources could fill this gap
    
    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "priority": self.priority.value,
            "impact_on_confidence": self.impact_on_confidence,
            "recommended_sources": list(self.recommended_sources),
        }


@dataclass(frozen=True)
class DataGapReport:
    """Complete gap analysis for one market."""
    market_id: str
    question: str
    total_gaps: int
    critical_gaps: int
    high_gaps: int
    medium_gaps: int
    low_gaps: int
    gaps: tuple[DataGap, ...]
    
    @property
    def has_critical_gaps(self) -> bool:
        return self.critical_gaps > 0
    
    @property
    def gap_summary(self) -> dict:
        """Summary of gap distribution."""
        return {
            "total": self.total_gaps,
            "kritisch": self.critical_gaps,
            "hoch": self.high_gaps,
            "mittel": self.medium_gaps,
            "niedrig": self.low_gaps,
        }
    
    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "question": self.question,
            "summary": self.gap_summary,
            "gaps": [g.as_dict() for g in self.gaps],
        }


def calculate_data_gaps(
    market_id: str,
    question: str,
    market_category: str | None,
    event_type: str | None,
    source_health: dict[str, dict] | None,
    historical_comparables_count: int,
    time_horizon_compatible: bool | None,
    has_structured_data: bool,
    has_event_relations: bool,
    resolution_path: ResolutionPath | None = None,
) -> DataGapReport:
    """Calculate data gaps for a market based on available information.
    
    This implements the "Data Gap Engine" principle:
    - Explicitly identify missing information
    - Prioritize by impact on forecast quality
    - Recommend specific sources to fill gaps
    
    Rules (documented, not tuned against data):
      - If no news evidence: NEWS_PRIMARY gap (HIGH priority)
      - If < 10 historical comparables: HISTORICAL_COMPARABLE gap (MEDIUM)
      - If time horizon unknown/incompatible: TIME_HORIZON gap (MEDIUM)
      - If no structured data available: STRUCTURED_DATA gap (HIGH)
      - If no event relations: EVENT_GRAPH gap (LOW - display only)
      - Category-specific gaps based on market type
    
    Returns a DataGapReport with all identified gaps sorted by priority.
    """
    gaps: list[DataGap] = []
    
    # News gaps
    # If market is in geopolitical/politics category and we have no primary news evidence
    # Real source_id values, matching `news/rss.py`'s DEFAULT_FEEDS keys and
    # the "gdelt" id `cli.py::_save_news_provider_health` now saves (audit
    # Part 4 wiring). Real bug fixed here (found while verifying that Part
    # 4's provider_health wiring actually flows into this gate): the old
    # tuple checked for source_id `"white_house"` (with an underscore),
    # which never matches the real feed name `"whitehouse"` (no
    # underscore, see `news/rss.py::DEFAULT_FEEDS`) — so even once
    # `provider_health` has real rows, a genuinely LIVE `whitehouse` feed
    # could never close this gap. Also added `state_department`, the third
    # real feed name directly relevant to POLITICS/GEOPOLITICS/WAR_PEACE
    # markets (federal_reserve/ecb/sec are not added here — they're not
    # topically primary sources for these three categories).
    if market_category in ("GEOPOLITICS", "WAR_PEACE", "POLITICS") and (
        source_health is None or not any(
            h.get("state") == "LIVE"
            for h in source_health.values()
            if h.get("source_id") in ("gdelt", "un_news", "whitehouse", "state_department")
        )
    ):
        # Real source routing (Data Gap Engine -> Source Registry): the RSS
        # feed names above (gdelt/un_news/whitehouse/state_department) are
        # the *live health-checked* primary feeds; `recommend_sources_for_gap`
        # additionally routes via the structured SourceDefinition registry
        # (e.g. reuters/apnews/congress_gov for the matching event_type),
        # so a market with a specific event_type gets concrete, registry-
        # backed source_ids rather than only the fixed RSS-feed tuple.
        routed = recommend_sources_for_gap(market_category, event_type)
        base_feeds = ("gdelt", "un_news", "whitehouse", "state_department")
        recommended = base_feeds + tuple(s for s in routed if s not in base_feeds)
        gaps.append(DataGap(
            category="NEWS_PRIMARY",
            severity="HIGH",
            description="Keine verifizierten Primärquellen (Regierungserklärungen, offizielle Pressemitteilungen).",
            priority=GapPriority.HIGH,
            impact_on_confidence=0.15,
            recommended_sources=recommended,
        ))
    
    # History is optional when a genuine structured domain-data path is
    # available; its absence is not a critical gap for that forecast.
    if not has_structured_data and historical_comparables_count < 10:
        if historical_comparables_count < 3:
            severity = "CRITICAL"
            impact = 0.30
        elif historical_comparables_count < 5:
            severity = "HIGH"
            impact = 0.20
        else:
            severity = "MEDIUM"
            impact = 0.10
        
        gaps.append(DataGap(
            category="HISTORICAL_COMPARABLE",
            severity=severity,
            description=f"Nur {historical_comparables_count} vergleichbare historische Fälle gefunden (weniger als empfohlene 10).",
            priority=GapPriority.MEDIUM,
            impact_on_confidence=impact,
            recommended_sources=(),  # Filled by internal history tracking
        ))
    
    # Time horizon gap
    if not has_structured_data and time_horizon_compatible is False:
        gaps.append(DataGap(
            category="TIME_HORIZON",
            severity="HIGH",
            description="Inkompatible Zeit-Horizonte zwischen Zielmarkt und Vergleichsfällen (z.B. 4-Jahres-Präsidentschaftsmarkt vs. 24-Tages-Ereignis).",
            priority=GapPriority.MEDIUM,
            impact_on_confidence=0.12,
            recommended_sources=(),  # Fixed by data collection improvements
        ))
    elif not has_structured_data and time_horizon_compatible is None:
        gaps.append(DataGap(
            category="TIME_HORIZON",
            severity="MEDIUM",
            description="Zeit-Horizontinformation fehlt für Vergleichsfälle.",
            priority=GapPriority.LOW,
            impact_on_confidence=0.05,
            recommended_sources=(),
        ))
    
    # Structured data gap (category-specific)
    if not has_structured_data:
        if market_category == "CRYPTO":
            gaps.append(DataGap(
                category="STRUCTURED_DATA",
                severity="HIGH",
                description="Kein echter CoinGecko-Preisdaten-Feed (nur historische Baseline).",
                priority=GapPriority.HIGH,
                impact_on_confidence=0.18,
                recommended_sources=("coingecko",),
            ))
        elif market_category == "CENTRAL_BANKS":
            # Real registry keys (was: "federal_reserve"/"eurostat", which
            # do not exist as SOURCE_REGISTRY ids and could never resolve to
            # an actual SourceDefinition). Routed via
            # MARKET_CATEGORY_TO_EVENT_TYPES["CENTRAL_BANKS"] -> fred/ecb.
            routed = recommend_sources_for_gap(market_category, event_type) or ("fred", "ecb")
            gaps.append(DataGap(
                category="STRUCTURED_DATA",
                severity="MEDIUM",
                description="Keine strukturierte Makro-Datenquelle (FRED, Eurostat) angeschlossen.",
                priority=GapPriority.MEDIUM,
                impact_on_confidence=0.10,
                recommended_sources=routed,
            ))
        elif market_category == "SPORT_OTHER":
            gaps.append(DataGap(
                category="STRUCTURED_DATA",
                severity="HIGH",
                description="Kein strukturierter Sports-Daten-Feed (ergebnisbasierte Prognose nicht möglich).",
                priority=GapPriority.HIGH,
                impact_on_confidence=0.15,
                recommended_sources=("sportsdb",),
            ))
    
    # Block D Part 3: concrete, step-named resolution-path gaps. Only ever
    # fires for markets with a real known multi-step structure
    # (resolution_path.applies=True) AND at least one step whose status is
    # genuinely "unknown" — never invented for markets with no known
    # structure (resolution_path is None or applies=False), which is the
    # honest, correct outcome for the overwhelming majority of markets.
    if resolution_path is not None and resolution_path.applies:
        unknown_steps = [s for s in resolution_path.steps if s.status == "unknown"]
        if unknown_steps:
            # The FIRST unknown step (in real process order) is the one that
            # actually blocks the path today — later unknown steps are
            # unknown only because this earlier one hasn't resolved yet, so
            # naming it specifically (not the whole list) is the concrete,
            # actionable gap description the project owner asked for.
            next_step = unknown_steps[0]
            step_label = _STEP_NAME_DE.get(next_step.name, next_step.name)
            severity = "HIGH" if resolution_path.deadline_pressure in ("HIGH", "CRITICAL") else "MEDIUM"
            prefix = "SEHR WICHTIG: " if severity == "HIGH" else "WICHTIG: "
            gaps.append(DataGap(
                category="RESOLUTION_PATH",
                severity=severity,
                description=(
                    f"{prefix}Status von '{step_label}' ({next_step.name}) im mehrstufigen "
                    f"Auflösungspfad ist unbekannt — keine datierte, primärquellen-taugliche "
                    f"Evidenz für diesen Schritt gefunden. "
                    f"{resolution_path.steps_remaining if resolution_path.steps_remaining is not None else '?'} "
                    f"von {len(resolution_path.steps)} Schritten insgesamt offen."
                ),
                priority=GapPriority.HIGH if severity == "HIGH" else GapPriority.MEDIUM,
                impact_on_confidence=0.15 if severity == "HIGH" else 0.08,
                recommended_sources=(),  # Filled by real evidence, not an external source feed
            ))
        if resolution_path.blockers:
            gaps.append(DataGap(
                category="RESOLUTION_PATH",
                severity="HIGH",
                description=(
                    "SEHR WICHTIG: Blockierender Schritt im Auflösungspfad erkannt: "
                    + "; ".join(resolution_path.blockers)
                ),
                priority=GapPriority.HIGH,
                impact_on_confidence=0.2,
                recommended_sources=(),
            ))

    # Event graph gap (display-only, never affects probability)
    if not has_event_relations:
        gaps.append(DataGap(
            category="EVENT_GRAPH",
            severity="LOW",
            description="Keine gespeicherten Event-Beziehungen für diesen Markt.",
            priority=GapPriority.LOW,
            impact_on_confidence=0.0,  # Event graph is for explainability only
            recommended_sources=(),  # Built by event extraction pipeline
        ))
    
    # Sort by priority (HIGH first, then MEDIUM, then LOW)
    gaps.sort(key=lambda g: (g.priority.value, -g.impact_on_confidence))
    
    # Count by severity
    critical = sum(1 for g in gaps if g.severity == "CRITICAL")
    high = sum(1 for g in gaps if g.severity == "HIGH")
    medium = sum(1 for g in gaps if g.severity == "MEDIUM")
    low = sum(1 for g in gaps if g.severity == "LOW")
    
    return DataGapReport(
        market_id=market_id,
        question=question,
        total_gaps=len(gaps),
        critical_gaps=critical,
        high_gaps=high,
        medium_gaps=medium,
        low_gaps=low,
        gaps=tuple(gaps),
    )


def get_priority_gaps(gaps: tuple[DataGap, ...], min_priority: GapPriority = GapPriority.HIGH) -> tuple[DataGap, ...]:
    """Filter gaps by minimum priority level."""
    return tuple(g for g in gaps if g.priority >= min_priority)


def get_recommendations(gaps: tuple[DataGap, ...]) -> set[str]:
    """Extract all recommended source IDs from gaps."""
    recommendations: set[str] = set()
    for gap in gaps:
        recommendations.update(gap.recommended_sources)
    return recommendations


def format_gap_report(report: DataGapReport) -> str:
    """Format gap report as human-readable German text."""
    lines = [
        f"Markt-ID: {report.market_id}",
        f"Fragen: {report.question}",
        "",
        f"Summe: {report.total_gaps} Datenlücken ({report.critical_gaps} kritisch, {report.high_gaps} hoch, {report.medium_gaps} mittel, {report.low_gaps} niedrig)",
        "",
        "Empfohlene Datenquellen:",
    ]
    
    recommendations = get_recommendations(report.gaps)
    if recommendations:
        lines.append(f"  - {', '.join(sorted(recommendations))}")
    else:
        lines.append("  - Keine externen Datenquellen empfohlen")
    
    if report.gaps:
        lines.append("")
        lines.append("Datenlücken:")
        for gap in report.gaps:
            lines.append(f"  [{gap.severity}] {gap.category}: {gap.description}")
    
    return "\n".join(lines)
