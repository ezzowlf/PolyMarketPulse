"""Data Source Registry & Provider Health Tracking

This module implements the Data Source Registry principle from the Data Intelligence Phase:
- Central registry of all data sources with structured metadata
- Provider health tracking (last success/failure, latency, data age, etc.)
- Frontend/API distinguishable states: LIVE, DEGRADED, STALE, OFFLINE, UNKNOWN

Key design principles:
- NO hardcoded fake universal source-quality numbers
- Source quality must be explainable (per-dimension breakdown)
- A provider being configured does NOT mean its data is available
- All health metrics are additive only (never overwritten)

This is the foundation for distinguishing:
  DATA → EVIDENCE → CONTEXT → HYPOTHESIS → FORECAST
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal

# Source class categories (spec: do not hardcode universal quality numbers)
SourceClass = Literal[
    "OFFICIAL_PRIMARY",          # Government, UN, official statements
    "STRUCTURED_MARKET_DATA",    # Exchange data, price feeds
    "STRUCTURED_STATISTICS",     # BLS, Eurostat, official stats
    "NEWS_PRIMARY",              # Reuters, AP, wire services
    "NEWS_SECONDARY",            # Aggregators, blogs, analysis
    "PRICE_DATA",                # CoinGecko, exchange APIs
    "SPORTS_DATA",               # Sports APIs, league feeds
    "SHIPPING_DATA",             # Marine tracking, port status
    "MACRO_DATA",                # Central bank feeds, economic releases
    "PREDICTION_MARKET",         # Polymarket, Manifold, etc.
    "OTHER",                     # Fallback for unclassified sources
]

# Source quality dimensions (spec: never a single universal number)
# Each dimension is 0..100, combined into a breakdown per source.
SOURCE_QUALITY_DIMENSIONS = (
    "reliability",       # 0..100: How trustworthy is this source?
    "freshness",         # 0..100: How fresh is the latest data?
    "completeness",      # 0..100: How complete is the data?
    "independence",      # 0..100: How independent from other sources?
    "historical_depth",  # 0..100: How far back does data go?
    "latency",           # 0..100: How fast is data delivery? (inverse of latency)
)


class ProviderHealthState(Enum):
    """Provider health state as visible to frontend/API.
    
    These are the states the UI should distinguish:
      LIVE      - Provider is operational and data is fresh
      DEGRADED  - Provider is operational but data quality is reduced
      STALE     - Provider returned data but it's too old
      OFFLINE   - Provider is not reachable or not responding
      UNKNOWN   - We have no information about this provider
    """
    LIVE = "LIVE"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceQualityBreakdown:
    """Every component behind the source quality score, so it is never
    a bare number without explanation (per the product requirement).
    
    This replaces the old "universal quality score" anti-pattern.
    Each dimension is 0..100, and the total is the average.
    """
    reliability: float      # 0..100
    freshness: float        # 0..100
    completeness: float     # 0..100
    independence: float     # 0..100
    historical_depth: float # 0..100
    latency: float          # 0..100

    @property
    def total(self) -> float:
        """Average of all dimensions, 0..100."""
        return round(
            (
                self.reliability
                + self.freshness
                + self.completeness
                + self.independence
                + self.historical_depth
                + self.latency
            ) / 6,
            1,
        )

    def as_dict(self) -> dict:
        return {
            "reliability": self.reliability,
            "freshness": self.freshness,
            "completeness": self.completeness,
            "independence": self.independence,
            "historical_depth": self.historical_depth,
            "latency": self.latency,
            "gesamt": self.total,
        }


@dataclass(frozen=True)
class DataSourceMetadata:
    """Complete metadata for one data source.
    
    This is the registry entry - everything we know about a source
    without having fetched from it yet.
    """
    source_id: str                   # Internal identifier (e.g., "gdelt", "coingecko", "reuters")
    name: str                        # Human-readable name (e.g., "GDELT News Archive")
    domain: str                      # Primary domain (e.g., "gdeltproject.org")
    source_type: str                 # Free-form type description
    market_domains: tuple[str, ...]  # Which market categories does this serve?
    source_class: SourceClass        # Categorized source class
    primary_or_secondary: Literal["primary", "secondary"]
    official: bool                   # Official source (government, exchange, etc.)
    structured_or_unstructured: Literal["structured", "unstructured", "mixed"]
    geographic_scope: str            # "global", "country", "region", etc.
    historical_depth_days: int       # How far back does data go?
    update_frequency_seconds: int    # Expected update interval
    expected_latency_seconds: int    # Expected latency from event to availability
    cost_usd_per_million: float      # Estimated cost (0 for free tiers)
    rate_limit: str                  # Rate limit description
    enabled: bool                    # Is this source enabled by default?
    # Note: reliability and independence_group are NOT stored here
    # because they can change over time based on actual provider health.
    # They are computed from provider_health data instead.


@dataclass(frozen=True)
class ProviderHealth:
    """Real-time health tracking for one provider.
    
    This is the dynamic state that changes based on actual fetch results.
    It is stored in the database and updated on every fetch attempt.
    """
    source_id: str
    last_success: datetime | None          # When did we last successfully fetch?
    last_failure: datetime | None          # When did we last fail?
    last_failure_reason: str | None        # Free-text reason for last failure
    last_http_status: int | None           # Last HTTP status code (if applicable)
    last_latency_ms: int | None            # Last fetch latency in ms
    consecutive_failures: int              # How many failures in a row?
    data_age_seconds: int | None           # Age of last successfully fetched data
    items_fetched: int                     # Total items fetched (lifetime)
    parse_failures: int                    # How many parse failures?
    
    def state(self, stale_threshold_seconds: int = 3600) -> ProviderHealthState:
        """Determine provider state based on health metrics.
        
        Rules (evaluated in order):
          1. If never fetched → UNKNOWN
          2. If last fetch > stale_threshold_seconds ago → STALE
          3. If last failure recent (within 1 hour) → OFFLINE
          4. If consecutive_failures >= 3 → OFFLINE
          5. If last_success but data is stale (data_age_seconds > stale_threshold) → STALE
          6. If last_success but latency > 2x expected → DEGRADED
          7. Otherwise → LIVE
        """
        if self.last_success is None and self.last_failure is None:
            return ProviderHealthState.UNKNOWN
        
        now = datetime.now(UTC)
        one_hour_ago = now - timedelta(hours=1)
        
        # Rule 2: Data is too old
        if self.data_age_seconds is not None and self.data_age_seconds > stale_threshold_seconds:
            return ProviderHealthState.STALE
        
        # Rule 3: Recent failure (within 1 hour)
        if self.last_failure is not None and self.last_failure >= one_hour_ago:
            return ProviderHealthState.OFFLINE
        
        # Rule 4: Multiple consecutive failures
        if self.consecutive_failures >= 3:
            return ProviderHealthState.OFFLINE
        
        # Rule 5: Data age from last success
        if self.last_success is not None:
            data_age = int((now - self.last_success).total_seconds())
            if data_age > stale_threshold_seconds:
                return ProviderHealthState.STALE
        
        # Rule 6: High latency
        # Note: This requires knowing the expected latency per source
        # For now, we use a reasonable default (60 seconds)
        if self.last_latency_ms is not None and self.last_latency_ms > 60000:
            return ProviderHealthState.DEGRADED
        
        # Rule 7: Everything looks good
        return ProviderHealthState.LIVE
    
    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_failure": self.last_failure.isoformat() if self.last_failure else None,
            "last_failure_reason": self.last_failure_reason,
            "last_http_status": self.last_http_status,
            "last_latency_ms": self.last_latency_ms,
            "consecutive_failures": self.consecutive_failures,
            "data_age_seconds": self.data_age_seconds,
            "items_fetched": self.items_fetched,
            "parse_failures": self.parse_failures,
            "state": self.state().value,
        }


# Default data sources registry (free, public, no-auth APIs only)
# This is NOT exhaustive - it's the initial set we can actually use today.
DEFAULT_DATA_SOURCES: tuple[DataSourceMetadata, ...] = (
    # --- Official / Primary Sources ---
    DataSourceMetadata(
        source_id="gdelt",
        name="GDELT DOC 2.1",
        domain="gdeltproject.org",
        source_type="News Archive",
        market_domains=("geopolitics", "politics", "macro"),
        source_class="NEWS_PRIMARY",
        primary_or_secondary="primary",
        official=False,  # GDELT is a research project, not an official source
        structured_or_unstructured="unstructured",
        geographic_scope="global",
        historical_depth_days=45,  # GDELT DOC 2.1 provides ~45 days
        update_frequency_seconds=3600,  # Hourly updates
        expected_latency_seconds=7200,  # ~2 hours latency
        cost_usd_per_million=0.0,
        rate_limit="Free tier: no explicit limit, but use reasonable rates",
        enabled=True,
    ),
    DataSourceMetadata(
        source_id="un_news",
        name="UN News Centre",
        domain="un.org",
        source_type="Official News Feed",
        market_domains=("geopolitics",),
        source_class="OFFICIAL_PRIMARY",
        primary_or_secondary="primary",
        official=True,
        structured_or_unstructured="unstructured",
        geographic_scope="global",
        historical_depth_days=365,  # About 1 year of RSS history
        update_frequency_seconds=1800,  # Every 30 minutes
        expected_latency_seconds=1800,  # ~30 minutes latency
        cost_usd_per_million=0.0,
        rate_limit="Free RSS feed, no rate limit",
        enabled=True,
    ),
    DataSourceMetadata(
        source_id="white_house",
        name="White House Press Briefings",
        domain="whitehouse.gov",
        source_type="Official Statement",
        market_domains=("politics", "geopolitics"),
        source_class="OFFICIAL_PRIMARY",
        primary_or_secondary="primary",
        official=True,
        structured_or_unstructured="structured",
        geographic_scope="us",
        historical_depth_days=365,
        update_frequency_seconds=3600,
        expected_latency_seconds=3600,
        cost_usd_per_million=0.0,
        rate_limit="Free RSS feed, no rate limit",
        enabled=True,
    ),
    # --- Price / Quant Data ---
    DataSourceMetadata(
        source_id="coingecko",
        name="CoinGecko Public API",
        domain="coingecko.com",
        source_type="Crypto Price Data",
        market_domains=("quant",),
        source_class="PRICE_DATA",
        primary_or_secondary="primary",
        official=False,
        structured_or_unstructured="structured",
        geographic_scope="global",
        historical_depth_days=90,  # Free tier provides 90 days
        update_frequency_seconds=60,  # Every minute
        expected_latency_seconds=30,  # ~30 seconds latency
        cost_usd_per_million=0.0,
        rate_limit="Free tier: 10,000 calls/month",
        enabled=True,
    ),
    DataSourceMetadata(
        source_id="polygon",
        name="Polygon.io (Free Tier)",
        domain="polygon.io",
        source_type="Stock Price Data",
        market_domains=("quant", "macro"),
        source_class="PRICE_DATA",
        primary_or_secondary="primary",
        official=False,
        structured_or_unstructured="structured",
        geographic_scope="us",
        historical_depth_days=30,  # Free tier provides 30 days
        update_frequency_seconds=60,
        expected_latency_seconds=30,
        cost_usd_per_million=0.0,
        rate_limit="Free tier: 5 calls/minute, 125,000/month",
        enabled=False,  # Disabled by default - requires API key for full features
    ),
    # --- Sports Data ---
    DataSourceMetadata(
        source_id="sportsdb",
        name="The Sports DB",
        domain="thesportsdb.com",
        source_type="Sports Events & Results",
        market_domains=("sports",),
        source_class="SPORTS_DATA",
        primary_or_secondary="primary",
        official=False,
        structured_or_unstructured="structured",
        geographic_scope="global",
        historical_depth_days=365,
        update_frequency_seconds=300,  # Every 5 minutes
        expected_latency_seconds=60,
        cost_usd_per_million=0.0,
        rate_limit="Free tier: 10 calls/minute",
        enabled=False,  # Disabled by default - requires API key
    ),
    # --- Macro Data ---
    DataSourceMetadata(
        source_id="federal_reserve",
        name="Federal Reserve Economic Data (FRED)",
        domain="fred.stlouisfed.org",
        source_type="Economic Statistics",
        market_domains=("macro",),
        source_class="STRUCTURED_STATISTICS",
        primary_or_secondary="primary",
        official=True,
        structured_or_unstructured="structured",
        geographic_scope="us",
        historical_depth_days=3650,  # 10 years
        update_frequency_seconds=86400,  # Daily updates
        expected_latency_seconds=7200,  # ~2 hours latency
        cost_usd_per_million=0.0,
        rate_limit="Free API: 50,000 calls/month",
        enabled=True,
    ),
    DataSourceMetadata(
        source_id="eurostat",
        name="Eurostat",
        domain="ec.europa.eu/eurostat",
        source_type="European Statistics",
        market_domains=("macro",),
        source_class="STRUCTURED_STATISTICS",
        primary_or_secondary="primary",
        official=True,
        structured_or_unstructured="structured",
        geographic_scope="eu",
        historical_depth_days=3650,
        update_frequency_seconds=86400,
        expected_latency_seconds=7200,
        cost_usd_per_million=0.0,
        rate_limit="Free API: no explicit limit",
        enabled=True,
    ),
    # --- Prediction Markets ---
    DataSourceMetadata(
        source_id="polymarket",
        name="Polymarket",
        domain="polymarket.com",
        source_type="Prediction Market Data",
        market_domains=("all"),  # All categories
        source_class="PREDICTION_MARKET",
        primary_or_secondary="primary",
        official=False,
        structured_or_unstructured="structured",
        geographic_scope="global",
        historical_depth_days=180,  # 6 months of history
        update_frequency_seconds=60,
        expected_latency_seconds=10,
        cost_usd_per_million=0.0,
        rate_limit="Public API: no authentication required",
        enabled=True,
    ),
)


def get_source_metadata(source_id: str) -> DataSourceMetadata | None:
    """Look up metadata for a source by its ID.
    
    Returns None if the source_id is not in our registry.
    This is the single source of truth for source metadata.
    """
    for source in DEFAULT_DATA_SOURCES:
        if source.source_id == source_id:
            return source
    return None


def get_source_quality_dimension_names() -> tuple[str, ...]:
    """Return the names of all source quality dimensions.
    
    This is used for UI display and audit trails.
    """
    return SOURCE_QUALITY_DIMENSIONS


def compute_source_quality_breakdown(
    source_id: str,
    health: ProviderHealth | None,
    metadata: DataSourceMetadata | None,
) -> SourceQualityBreakdown:
    """Compute source quality breakdown for a provider.
    
    This replaces the old "universal quality score" anti-pattern.
    The result is never a single number - it's always a breakdown with
    explainable dimensions.
    
    Rules (documented, not tuned against data):
      - reliability: 100 if official, 80 if primary, 50 if secondary, 30 otherwise
      - freshness: 100 if last_success within 1h, 50 if within 24h, 20 otherwise
      - completeness: 100 if structured, 70 if mixed, 50 if unstructured
      - independence: 100 if primary, 60 if secondary
      - historical_depth: 100 if >365 days, 70 if >90 days, 40 if >30 days, 20 otherwise
      - latency: 100 if <60s, 70 if <300s, 40 if <1800s, 20 otherwise
    """
    # Start with defaults if we don't have health or metadata
    current_health = health
    current_metadata = metadata or DataSourceMetadata(
        source_id=source_id,
        name="Unknown Source",
        domain="unknown",
        source_type="unknown",
        market_domains=(),
        source_class="OTHER",
        primary_or_secondary="secondary",
        official=False,
        structured_or_unstructured="unstructured",
        geographic_scope="global",
        historical_depth_days=30,
        update_frequency_seconds=3600,
        expected_latency_seconds=300,
        cost_usd_per_million=0.0,
        rate_limit="Unknown",
        enabled=True,
    )
    
    now = datetime.now(UTC)
    
    # 1. reliability
    if current_metadata.official:
        reliability = 100.0
    elif current_metadata.primary_or_secondary == "primary":
        reliability = 80.0
    else:
        reliability = 50.0
    
    # 2. freshness (based on health)
    if current_health and current_health.last_success:
        age_hours = (now - current_health.last_success).total_seconds() / 3600
        if age_hours < 1:
            freshness = 100.0
        elif age_hours < 24:
            freshness = 70.0
        elif age_hours < 48:
            freshness = 40.0
        else:
            freshness = 20.0
    else:
        freshness = 30.0  # No recent success
    
    # 3. completeness
    if current_metadata.structured_or_unstructured == "structured":
        completeness = 100.0
    elif current_metadata.structured_or_unstructured == "mixed":
        completeness = 70.0
    else:
        completeness = 50.0
    
    # 4. independence
    if current_metadata.primary_or_secondary == "primary":
        independence = 100.0
    else:
        independence = 60.0
    
    # 5. historical_depth
    depth_days = current_metadata.historical_depth_days
    if depth_days > 365:
        historical_depth = 100.0
    elif depth_days > 90:
        historical_depth = 70.0
    elif depth_days > 30:
        historical_depth = 40.0
    else:
        historical_depth = 20.0
    
    # 6. latency (based on expected latency from metadata)
    expected_latency = current_metadata.expected_latency_seconds
    if expected_latency < 60:
        latency = 100.0
    elif expected_latency < 300:
        latency = 70.0
    elif expected_latency < 1800:
        latency = 40.0
    else:
        latency = 20.0
    
    return SourceQualityBreakdown(
        reliability=reliability,
        freshness=freshness,
        completeness=completeness,
        independence=independence,
        historical_depth=historical_depth,
        latency=latency,
    )


# --- Provider Health Database Helpers ---

def create_provider_health_table_sql() -> str:
    """Return SQL to create the provider_health table.
    
    This is used by migrations.py to add the table to the database.
    """
    return """
        CREATE TABLE IF NOT EXISTS provider_health (
            source_id TEXT PRIMARY KEY,
            last_success TEXT,
            last_failure TEXT,
            last_failure_reason TEXT,
            last_http_status INTEGER,
            last_latency_ms INTEGER,
            consecutive_failures INTEGER DEFAULT 0,
            data_age_seconds INTEGER,
            items_fetched INTEGER DEFAULT 0,
            parse_failures INTEGER DEFAULT 0
        );
    """


def provider_health_to_row(health: ProviderHealth) -> tuple:
    """Convert ProviderHealth to a database row tuple."""
    return (
        health.source_id,
        health.last_success.isoformat() if health.last_success else None,
        health.last_failure.isoformat() if health.last_failure else None,
        health.last_failure_reason,
        health.last_http_status,
        health.last_latency_ms,
        health.consecutive_failures,
        health.data_age_seconds,
        health.items_fetched,
        health.parse_failures,
    )


def row_to_provider_health(row: tuple) -> ProviderHealth:
    """Convert a database row to ProviderHealth."""
    return ProviderHealth(
        source_id=row[0],
        last_success=datetime.fromisoformat(row[1]) if row[1] else None,
        last_failure=datetime.fromisoformat(row[2]) if row[2] else None,
        last_failure_reason=row[3],
        last_http_status=row[4],
        last_latency_ms=row[5],
        consecutive_failures=row[6],
        data_age_seconds=row[7],
        items_fetched=row[8],
        parse_failures=row[9],
    )