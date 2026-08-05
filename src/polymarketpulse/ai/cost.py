from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

# Official per-1M-token USD prices as of this writing. These change over
# time — treat this table as a snapshot to keep estimates roughly honest,
# not as a live-updated source of truth. Actual cost after each real call is
# still computed from these same rates applied to the token counts OpenAI
# actually reports, which is what gets stored — never a made-up number.
PRICING_USD_PER_MILLION_TOKENS = {
    "gpt-5-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.40},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10, "output": 1.60},
}


@dataclass(frozen=True)
class CostEstimate:
    model: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> CostEstimate:
    rates = PRICING_USD_PER_MILLION_TOKENS.get(model, PRICING_USD_PER_MILLION_TOKENS["gpt-5-nano"])
    cost = (input_tokens / 1_000_000) * rates["input"] + (output_tokens / 1_000_000) * rates["output"]
    return CostEstimate(
        model=model,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_cost_usd=round(cost, 6),
    )


def actual_cost(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
    rates = PRICING_USD_PER_MILLION_TOKENS.get(model, PRICING_USD_PER_MILLION_TOKENS["gpt-5-nano"])
    billable_input = max(0, input_tokens - cached_input_tokens)
    cost = (
        (billable_input / 1_000_000) * rates["input"]
        + (cached_input_tokens / 1_000_000) * rates["cached_input"]
        + (output_tokens / 1_000_000) * rates["output"]
    )
    return round(cost, 6)


def estimate_tokens_from_text(text: str) -> int:
    """Rough, conservative token estimate (~4 chars/token for English/German
    mixed text) used only for the pre-flight budget check — the real number
    always comes from the API response afterwards."""
    return max(1, len(text) // 4)


def spent_today_usd(conn: sqlite3.Connection) -> float:
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    row = conn.execute(
        "SELECT COALESCE(SUM(actual_cost_usd), 0) FROM ai_analysis_runs WHERE created_at >= ?",
        (since,),
    ).fetchone()
    return round(row[0] or 0.0, 6)


def within_daily_budget(conn: sqlite3.Connection, daily_budget_usd: float, additional_cost_usd: float) -> bool:
    return spent_today_usd(conn) + additional_cost_usd <= daily_budget_usd
