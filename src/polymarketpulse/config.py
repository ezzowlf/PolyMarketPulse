from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env(*names: str, default: str | None = None) -> str | None:
    """Read the first set env var among `names` (new prefixed name first)."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    database_path: Path
    default_provider: str
    scan_limit: int
    request_timeout: float
    min_liquidity: float
    min_volume_24h: float
    alert_score: float
    store_unchanged_snapshots: bool
    news_enabled: bool
    telegram_enabled: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    ai_enabled: bool
    openai_api_key: str | None
    openai_model: str
    openai_fallback_model: str
    openai_timeout_seconds: float
    openai_max_output_tokens: int
    ai_cache_ttl_seconds: int
    openai_max_cost_per_analysis_usd: float
    openai_max_input_tokens: int
    openai_daily_budget_usd: float
    openai_reasoning_effort: str | None
    openai_escalation_enabled: bool

    @classmethod
    def load(cls) -> Settings:
        load_dotenv()
        return cls(
            environment=_env("POLYMARKETPULSE_ENV", default="development") or "development",
            database_path=Path(
                _env("POLYMARKETPULSE_DATABASE_PATH", "DATABASE_PATH")
                or "data/polymarketpulse.db"
            ),
            default_provider=_env("POLYMARKETPULSE_DEFAULT_PROVIDER", default="polymarket")
            or "polymarket",
            scan_limit=int(_env("POLYMARKETPULSE_SCAN_LIMIT", "SCAN_LIMIT") or "100"),
            request_timeout=float(
                _env("POLYMARKETPULSE_REQUEST_TIMEOUT", "REQUEST_TIMEOUT") or "20"
            ),
            min_liquidity=float(_env("MIN_LIQUIDITY") or "5000"),
            min_volume_24h=float(_env("MIN_VOLUME_24H") or "1000"),
            alert_score=float(_env("ALERT_SCORE") or "70"),
            store_unchanged_snapshots=_bool(
                _env("POLYMARKETPULSE_STORE_UNCHANGED_SNAPSHOTS", default="false")
            ),
            news_enabled=_bool(_env("POLYMARKETPULSE_NEWS_ENABLED", default="false")),
            telegram_enabled=_bool(_env("POLYMARKETPULSE_TELEGRAM_ENABLED", default="false")),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
            ai_enabled=_bool(_env("POLYMARKETPULSE_AI_ENABLED", default="false")),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=_env("OPENAI_MODEL", default="gpt-5-nano") or "gpt-5-nano",
            openai_fallback_model=_env("OPENAI_FALLBACK_MODEL", default="gpt-5-mini") or "gpt-5-mini",
            openai_timeout_seconds=float(_env("OPENAI_TIMEOUT_SECONDS") or "30"),
            # Reasoning models (gpt-5-*) spend part of max_output_tokens on
            # internal reasoning before writing the final answer — 1500 was
            # too tight and produced empty responses (status=incomplete,
            # incomplete_reason=max_output_tokens). 2000 leaves realistic
            # headroom for both reasoning and the compact JSON explanation.
            openai_max_output_tokens=int(_env("OPENAI_MAX_OUTPUT_TOKENS") or "2000"),
            ai_cache_ttl_seconds=int(_env("POLYMARKETPULSE_AI_CACHE_TTL_SECONDS") or "900"),
            openai_max_cost_per_analysis_usd=float(_env("OPENAI_MAX_COST_PER_ANALYSIS_USD") or "0.01"),
            openai_max_input_tokens=int(_env("OPENAI_MAX_INPUT_TOKENS") or "10000"),
            openai_daily_budget_usd=float(_env("OPENAI_DAILY_BUDGET_USD") or "1.00"),
            openai_reasoning_effort=_env("OPENAI_REASONING_EFFORT", default="low") or None,
            openai_escalation_enabled=_bool(_env("OPENAI_ESCALATION_ENABLED", default="false")),
        )

    @property
    def ai_ready(self) -> bool:
        """AI is only usable when explicitly enabled AND a key is present."""
        return self.ai_enabled and bool(self.openai_api_key)
