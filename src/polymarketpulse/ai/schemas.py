from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DISCLAIMER = "Research-Hinweis – keine Wettaufforderung, kein sicherer Gewinn."

PROMPT_VERSION = "v1"


class SupportingFactor(BaseModel):
    factor: str
    evidence: str
    strength: Literal["low", "medium", "high"]

    model_config = {"extra": "forbid"}


class AnalysisResult(BaseModel):
    """Structured AI output. Every field is grounded in the bounded context
    the backend supplied — the model is instructed never to invent data, and
    this schema is enforced via OpenAI Structured Outputs (JSON Schema)."""

    summary: str
    supporting_factors: list[SupportingFactor] = Field(default_factory=list)
    opposing_factors: list[SupportingFactor] = Field(default_factory=list)
    relevant_news: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    market_move_explanation: str
    confidence_in_analysis: float = Field(ge=0.0, le=1.0)
    source_ids: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER

    model_config = {"extra": "forbid"}


class MarketContext(BaseModel):
    """Bounded, pre-filtered context handed to the model. Nothing beyond
    this object is ever sent — the AI never touches SQLite directly."""

    market_id: str
    provider: str
    question: str
    description: str | None = None
    category: str | None = None
    resolution_status: str
    yes_price: float | None = None
    no_price: float | None = None
    spread: float | None = None
    liquidity: float | None = None
    volume_24h: float | None = None
    days_to_resolution: float | None = None
    price_history: list[dict] = Field(default_factory=list)
    data_quality_score: float | None = None
    data_quality_issues: list[str] = Field(default_factory=list)
    research_signals: list[dict] = Field(default_factory=list)
    relevant_news: list[dict] = Field(default_factory=list)
    comparable_confirmed_markets: list[dict] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class AIStatusResponse(BaseModel):
    enabled: bool
    ready: bool
    model: str
    cache_ttl_seconds: int
    reason: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    market_id: str | None = None


class CompareRequest(BaseModel):
    market_ids: list[str] = Field(min_length=2, max_length=5)


class AIRunMeta(BaseModel):
    analysis_id: int
    model: str
    prompt_version: str
    cached: bool
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: str


class AIAnalysisResponse(BaseModel):
    result: AnalysisResult
    meta: AIRunMeta
