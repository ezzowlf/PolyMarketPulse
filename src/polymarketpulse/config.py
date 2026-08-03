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
        )
