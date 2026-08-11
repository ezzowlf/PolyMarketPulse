"""Source Registry - Catalog und Routing für Quellen basierend auf Marktart.

Dieses Modul definiert:
1. Source Catalog: Welche Quellen gibt es für welche Marktarten?
2. Source Quality: Primary/Secondary/Other Tagging
3. Source Independence: Cluster-Erkennung (mehrere Artikel von gleicher Quelle = 1 Bestätigung)
4. Source Performance: Historische Zuverlässigkeit tracken
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class SourceType(str, Enum):
    """Typ der Quelle für Routing und Quality-Bewertung."""
    PRIMARY_OFFICIAL = "primary_official"  # offizielle Quelle (Congress.gov, White House, etc.)
    SECONDARY_REPUTABLE = "secondary_reputable"  # seriöse Agenturen (Reuters, AP, etc.)
    SECONDARY_STANDARD = "secondary_standard"  # etablierte Medien (FAZ, SZ, Spiegel, etc.)
    OTHER = "other"  # andere Quellen (Blogs, SoMed, etc.)
    AI_LLM = "ai_llm"  # KI/LLM (darf keine Primärquelle sein)
    MODEL_ASSUMPTION = "model_assumption"  # Modellannahme (keine Evidenz)


class SourceDomain(str, Enum):
    """Domain/Themenbereich der Quelle."""
    CONGRESS = "congress"  # Gesetzgebung
    WHITE_HOUSE = "white_house"  # US-Präsident
    SENATE = "senate"  # US-Senat
    HOUSE = "house"  # US-Repräsentantenhaus
    FED = "fed"  # US-Notenbank
    ECB = "ecb"  # EZB
    BLS = "bls"  # Bureau of Labor Statistics
    BEA = "bea"  # Bureau of Economic Analysis
    EUR_STAT = "europa"  # Eurostat
    REUTERS = "reuters"  # Reuters
    AP_NEWS = "apnews"  # Associated Press
    DPA = "dpa"  # Deutsche Presse-Agentur
    FAZ = "faz"  # Frankfurter Allgemeine Zeitung
    SZ = "sz"  # Süddeutsche Zeitung
    SPIEGEL = "spiegel"  # Der Spiegel
    BILD = "bild"  # Bild
    COINGECKO = "coingecko"  # Krypto-Daten
    FRED = "fred"  # FRED Economic Data
    # Weitere...


@dataclass(frozen=True)
class SourceDefinition:
    """Definition einer Quelle für Routing und Quality."""
    domain: SourceDomain
    source_name: str  # Interner Name (z.B. "reuters")
    display_name: str  # Anzeigename (z.B. "Reuters")
    source_type: SourceType
    relevance_by_event_type: dict[str, Literal["HIGH", "MEDIUM", "LOW", "NONE"]]
    # Z.B. {"legislation": "HIGH", "ceasefire": "MEDIUM", "sport_match": "NONE"}
    
    # Independence: Quellen, die als "gleiche Quelle" gelten (für Cluster-Erkennung)
    # Z.B. Reuters-Subdomains alle zum gleichen Cluster gehören
    independence_group: str | None = None
    
    # Historische Zuverlässigkeit (wird später datenbasiert gelernt)
    base_reliability: float = 0.7  # 0..1
    
    # Priority für Source Fetching (wenn Daten fehlen)
    fetch_priority: int = 5  # 1=hoch, 10=niedrig


# Source Registry - definiert alle bekannten Quellen
SOURCE_REGISTRY: dict[str, SourceDefinition] = {
    # Gesetzgebung
    "congress_gov": SourceDefinition(
        domain=SourceDomain.CONGRESS,
        source_name="congress.gov",
        display_name="Congress.gov",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "legislation": "HIGH",
            "election": "MEDIUM",
            "appointment": "MEDIUM",
        },
        independence_group="us_government",
        base_reliability=0.95,
        fetch_priority=1,
    ),
    "senate_gov": SourceDefinition(
        domain=SourceDomain.SENATE,
        source_name="senate.gov",
        display_name="Senate.gov",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "legislation": "HIGH",
            "appointment": "HIGH",
        },
        independence_group="us_government",
        base_reliability=0.95,
        fetch_priority=1,
    ),
    "house_gov": SourceDefinition(
        domain=SourceDomain.HOUSE,
        source_name="house.gov",
        display_name="House.gov",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "legislation": "HIGH",
            "election": "MEDIUM",
        },
        independence_group="us_government",
        base_reliability=0.95,
        fetch_priority=1,
    ),
    "white_house": SourceDefinition(
        domain=SourceDomain.WHITE_HOUSE,
        source_name="whitehouse.gov",
        display_name="White House",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "office_departure": "HIGH",
            "appointment": "HIGH",
            "ceasefire": "MEDIUM",
            "sanctions": "MEDIUM",
        },
        independence_group="us_government",
        base_reliability=0.95,
        fetch_priority=1,
    ),
    # Zentralbanken
    "fred": SourceDefinition(
        domain=SourceDomain.FRED,
        source_name="fred.stlouisfed.org",
        display_name="FRED",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "central_bank_decision": "HIGH",
            "rate_hike": "HIGH",
            "rate_cut": "HIGH",
            "rate_hold": "HIGH",
            "monetary_policy": "HIGH",
        },
        independence_group="fed",
        base_reliability=0.95,
        fetch_priority=1,
    ),
    "ecb": SourceDefinition(
        domain=SourceDomain.ECB,
        source_name="ecb.europa.eu",
        display_name="ECB",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "central_bank_decision": "HIGH",
            "monetary_policy": "HIGH",
        },
        independence_group="ecb",
        base_reliability=0.95,
        fetch_priority=1,
    ),
    # Statistik
    "bls": SourceDefinition(
        domain=SourceDomain.BLS,
        source_name="bls.gov",
        display_name="Bureau of Labor Statistics",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "monetary_policy": "MEDIUM",
            "policy_change": "MEDIUM",
        },
        independence_group="us_government",
        base_reliability=0.9,
        fetch_priority=2,
    ),
    # Nachrichtenagenturen (Primary/Secondary)
    "reuters": SourceDefinition(
        domain=SourceDomain.REUTERS,
        source_name="reuters.com",
        display_name="Reuters",
        source_type=SourceType.SECONDARY_REPUTABLE,
        relevance_by_event_type={
            "ceasefire": "HIGH",
            "war_escalation": "HIGH",
            "military_action": "HIGH",
            "sanctions": "MEDIUM",
            "territorial_control": "MEDIUM",
            "diplomatic_agreement": "MEDIUM",
            "office_departure": "MEDIUM",
            "election": "MEDIUM",
            "appointment": "MEDIUM",
            "legislation": "MEDIUM",
        },
        independence_group="reuters_ap",
        base_reliability=0.8,
        fetch_priority=2,
    ),
    "apnews": SourceDefinition(
        domain=SourceDomain.AP_NEWS,
        source_name="apnews.com",
        display_name="Associated Press",
        source_type=SourceType.SECONDARY_REPUTABLE,
        relevance_by_event_type={
            "ceasefire": "HIGH",
            "war_escalation": "HIGH",
            "military_action": "HIGH",
            "sanctions": "MEDIUM",
            "office_departure": "MEDIUM",
            "election": "MEDIUM",
            "appointment": "MEDIUM",
            "legislation": "MEDIUM",
        },
        independence_group="reuters_ap",
        base_reliability=0.8,
        fetch_priority=2,
    ),
    "dpa": SourceDefinition(
        domain=SourceDomain.DPA,
        source_name="dpa.de",
        display_name="Deutsche Presse-Agentur",
        source_type=SourceType.SECONDARY_REPUTABLE,
        relevance_by_event_type={
            "ceasefire": "MEDIUM",
            "war_escalation": "MEDIUM",
            "office_departure": "LOW",
            "election": "LOW",
            "appointment": "LOW",
            "legislation": "LOW",
        },
        independence_group="dpa",
        base_reliability=0.75,
        fetch_priority=3,
    ),
    # etablierte Medien
    "faz": SourceDefinition(
        domain=SourceDomain.FAZ,
        source_name="faz.net",
        display_name="Frankfurter Allgemeine Zeitung",
        source_type=SourceType.SECONDARY_STANDARD,
        relevance_by_event_type={
            "ceasefire": "MEDIUM",
            "war_escalation": "MEDIUM",
            "office_departure": "LOW",
            "election": "LOW",
            "appointment": "LOW",
            "legislation": "LOW",
        },
        independence_group="faz_sz_spiegel",
        base_reliability=0.7,
        fetch_priority=4,
    ),
    "sz": SourceDefinition(
        domain=SourceDomain.SZ,
        source_name="sueddeutsche.de",
        display_name="Süddeutsche Zeitung",
        source_type=SourceType.SECONDARY_STANDARD,
        relevance_by_event_type={
            "ceasefire": "MEDIUM",
            "war_escalation": "MEDIUM",
            "office_departure": "LOW",
            "election": "LOW",
            "appointment": "LOW",
            "legislation": "LOW",
        },
        independence_group="faz_sz_spiegel",
        base_reliability=0.7,
        fetch_priority=4,
    ),
    "spiegel": SourceDefinition(
        domain=SourceDomain.SPIEGEL,
        source_name="spiegel.de",
        display_name="Der Spiegel",
        source_type=SourceType.SECONDARY_STANDARD,
        relevance_by_event_type={
            "ceasefire": "MEDIUM",
            "war_escalation": "MEDIUM",
            "office_departure": "LOW",
            "election": "LOW",
            "appointment": "LOW",
            "legislation": "LOW",
        },
        independence_group="faz_sz_spiegel",
        base_reliability=0.7,
        fetch_priority=4,
    ),
    # Krypto
    "coingecko": SourceDefinition(
        domain=SourceDomain.COINGECKO,
        source_name="coingecko.com",
        display_name="CoinGecko",
        source_type=SourceType.PRIMARY_OFFICIAL,
        relevance_by_event_type={
            "price_above": "HIGH",
            "price_below": "HIGH",
        },
        independence_group="coingecko",
        base_reliability=0.85,
        fetch_priority=1,
    ),
}


def get_source_definition(source_domain: str) -> SourceDefinition | None:
    """Hole SourceDefinition für einen Domain-Namen."""
    return SOURCE_REGISTRY.get(source_domain)


def get_source_id_for_definition(source_def: SourceDefinition) -> str | None:
    """Reverse-lookup the registry key (source_id) for a SourceDefinition."""
    for key, value in SOURCE_REGISTRY.items():
        if value is source_def:
            return key
    return None


# Market-category -> event_type keys used by SourceDefinition.relevance_by_event_type.
# This is the Data Gap Engine's routing table: a market_category alone (no
# specific event_type detected yet) still needs a real source recommendation,
# so each category maps to the event_type keys most representative of it.
# Kept intentionally narrow/explicit (no guessed categories) so a gap's
# recommended_sources are always traceable back to a real relevance entry.
MARKET_CATEGORY_TO_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "LEGISLATION": ("legislation",),
    "POLITICS": ("appointment", "office_departure", "court_outcome"),
    "ELECTIONS": ("election",),
    "GEOPOLITICS": ("ceasefire", "war_escalation", "sanctions", "diplomatic_agreement"),
    "WAR_PEACE": ("war_escalation", "ceasefire", "military_action", "territorial_control"),
    "CENTRAL_BANKS": ("central_bank_decision", "rate_cut", "rate_hike", "rate_hold", "monetary_policy"),
    "CRYPTO": ("price_above", "price_below"),
}


def get_source_ids_for_event_type(event_type: str | None) -> tuple[str, ...]:
    """Registry keys (source_ids) relevant to an event_type, HIGH-relevance first."""
    if not event_type:
        return ()
    return tuple(
        get_source_id_for_definition(s) or s.source_name
        for s in get_sources_for_event_type(event_type)
    )


def recommend_sources_for_gap(
    market_category: str | None, event_type: str | None = None
) -> tuple[str, ...]:
    """Real source routing for the Data Gap Engine: Market Category / Event
    Type -> concrete, registry-backed source_ids (not a disconnected list).

    Prefers the specific event_type (e.g. "legislation") when known; falls
    back to the category's mapped event_type keys via
    `MARKET_CATEGORY_TO_EVENT_TYPES` (e.g. CENTRAL_BANKS -> fred, ecb)
    otherwise. Dedupes while preserving priority order.
    """
    candidate_event_types: list[str] = []
    if event_type:
        candidate_event_types.append(event_type)
    if market_category:
        candidate_event_types.extend(MARKET_CATEGORY_TO_EVENT_TYPES.get(market_category, ()))

    seen: set[str] = set()
    ordered: list[str] = []
    for et in candidate_event_types:
        for source_id in get_source_ids_for_event_type(et):
            if source_id not in seen:
                seen.add(source_id)
                ordered.append(source_id)
    return tuple(ordered)


def get_sources_for_event_type(event_type: str) -> list[SourceDefinition]:
    """Hole alle Quellen, die für einen Event-Typ relevant sind."""
    relevant = []
    for source in SOURCE_REGISTRY.values():
        relevance = source.relevance_by_event_type.get(event_type, "NONE")
        if relevance in ("HIGH", "MEDIUM"):
            relevant.append(source)
    # Sortiere nach relevance (HIGH first) und fetch_priority
    relevant.sort(key=lambda s: (
        0 if s.relevance_by_event_type.get(event_type) == "HIGH" else 1,
        s.fetch_priority
    ))
    return relevant


def _resolve_cluster_key(source_id: str, source_label: str | None) -> str:
    """Pick the identifier to cluster/score a piece of evidence under.

    Evidence carries two identifiers: a URL domain (`source_id`, e.g.
    "example.com") and a curated source label (`source_label`, e.g.
    "federal_reserve"). Prefer whichever one the registry actually
    recognizes, falling back to the domain when neither is known — this
    keeps a first-party feed on an anonymized/placeholder domain (as in
    tests, and some RSS-only feeds in production) from being clustered
    with, and scored identically to, an arbitrary unknown domain.
    """
    if get_source_definition(source_id) is not None:
        return source_id
    if source_label and get_source_definition(source_label) is not None:
        return source_label
    return source_id


def calculate_source_independence(
    source_ids: list[str], source_labels: list[str | None] | None = None
) -> tuple[int, dict[str, int]]:
    """
    Berechne echte unabhängige Bestätigungen.

    Args:
        source_ids: Liste von Quell-Domains (z.B. ["reuters.com", "apnews.com", "reuters.com"])
        source_labels: Optionale parallele Liste curierter Quell-Labels
            (z.B. ["federal_reserve", ...]), verwendet als Fallback wenn die
            Domain selbst nicht in der Registry bekannt ist.

    Returns:
        (anzahl_unabhängiger_bestätigungen, cluster_counts)
        Z.B. (2, {"reuters_ap": 1, "apnews": 1}) - Reuters und AP zählen als 1 Cluster
    """
    independent_count, cluster_counts, _ = _cluster_sources(source_ids, source_labels)
    return independent_count, cluster_counts


def _cluster_sources(
    source_ids: list[str], source_labels: list[str | None] | None = None
) -> tuple[int, dict[str, int], dict[str, str]]:
    """Shared clustering logic. Returns (independent_count, cluster_counts,
    cluster_trust_labels) where `cluster_trust_labels` maps each cluster key
    to the best identifier to use for a curated-trust-table fallback lookup
    (preferring a real source label like "federal_reserve" over an opaque
    URL domain like "example.com" when the registry doesn't know either)."""
    cluster_counts: dict[str, int] = {}
    cluster_trust_labels: dict[str, str] = {}
    labels = source_labels or [None] * len(source_ids)

    for source_id, source_label in zip(source_ids, labels):
        key = _resolve_cluster_key(source_id, source_label)
        source_def = get_source_definition(key)
        if source_def is None:
            # Unbekannte Quelle als eigenes Cluster
            cluster = key
        else:
            # Verwende independence_group oder domain als cluster
            cluster = source_def.independence_group or key

        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        if cluster not in cluster_trust_labels:
            cluster_trust_labels[cluster] = source_label or key

    independent_count = len(cluster_counts)
    return independent_count, cluster_counts, cluster_trust_labels


def calculate_source_quality_score(
    source_ids: list[str], source_labels: list[str | None] | None = None
) -> float:
    """
    Berechne Gesamt-Qualität der Quellen (0..100).

    Berücksichtigt:
    - Primary/Secondary/Other Type
    - Anzahl unabhängiger Bestätigungen
    - Historische Zuverlässigkeit

    `source_labels` is an optional parallel list of curated source labels
    (see `_resolve_cluster_key`) used when the raw domain isn't in the
    registry — without it, evidence published under a generic/placeholder
    domain loses all trust differentiation from a fully unknown source.
    """
    if not source_ids:
        return 0.0

    labels = source_labels or [None] * len(source_ids)
    resolved_keys = [_resolve_cluster_key(sid, lbl) for sid, lbl in zip(source_ids, labels)]
    independent_count, cluster_counts, cluster_trust_labels = _cluster_sources(source_ids, labels)

    # Weighted average reliability
    total_weight = 0.0
    weighted_sum = 0.0

    for cluster, count in cluster_counts.items():
        # Hole die Zuverlässigkeit des erste Quelle im Cluster
        cluster_source = None
        for key in resolved_keys:
            source_def = get_source_definition(key)
            if source_def and (source_def.independence_group == cluster or source_def.source_name == cluster):
                cluster_source = source_def
                break

        if cluster_source is None:
            # Not in the structured registry (yet). Fall back to the older
            # curated per-source trust table (`prediction/news.py`) instead
            # of a flat 0.6 — that table already carries real trust signal
            # for official/primary feeds (federal_reserve, sec, ecb, ...)
            # and top-tier wire services that haven't been migrated into
            # SourceDefinition entries here. Without this fallback, a
            # first-party Fed statement and a random blog both collapsed to
            # the same 0.6 default and lost all trust differentiation. Use
            # the curated source *label* for this lookup, not the cluster
            # key, since the cluster key may be an opaque/placeholder URL
            # domain that carries no trust signal of its own.
            from .prediction.news import _trust_for_source

            reliability = _trust_for_source(cluster_trust_labels.get(cluster, cluster))
        else:
            reliability = cluster_source.base_reliability

        # Bonus für Primary Sources
        if cluster_source and cluster_source.source_type == SourceType.PRIMARY_OFFICIAL:
            reliability = min(1.0, reliability + 0.1)

        weighted_sum += reliability * count
        total_weight += count

    if total_weight == 0:
        return 0.0
    
    avg_reliability = weighted_sum / total_weight
    
    # Bonus für viele unabhängige Quellen
    independence_bonus = min(0.2, independent_count * 0.05)
    
    score = (avg_reliability + independence_bonus) * 100
    return round(min(100.0, score), 1)


# Test
if __name__ == "__main__":
    # Test: Reuters und AP zählen als 1 Cluster
    sources = ["reuters.com", "apnews.com", "congress.gov"]
    independent, clusters = calculate_source_independence(sources)
    print(f"Unabhängige Bestätigungen: {independent}")
    print(f"Cluster: {clusters}")
    # Erwartet: 2 (reuters_ap + congress_gov)
    
    quality = calculate_source_quality_score(sources)
    print(f"Source Quality Score: {quality}")
    # Erwartet: ca. 85-90 (hohe Zuverlässigkeit + unabhängige Quellen)