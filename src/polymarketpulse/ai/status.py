"""Stable, machine-readable status/error codes for every outcome an
explanation attempt (or the analysis as a whole) can have — introduced
after a live GPT-5-nano smoke test revealed that the previous fallback path
collapsed every possible failure (timeout, invalid JSON, schema mismatch,
inconsistent numbers, budget block, ...) into an identical, information-
free persisted row (`input_tokens=None`, `actual_cost_usd=0.0`), making it
impossible to tell afterwards whether a real, billable OpenAI call had even
happened.
"""

from __future__ import annotations

from dataclasses import dataclass

# Per-attempt and run-level final status codes. Deliberately a flat set of
# strings (not an Enum) so they store directly as TEXT and stay trivially
# forward-compatible if new codes are added later.
AI_STATUS_SUCCESS = "success"
AI_STATUS_DISABLED = "disabled"
AI_STATUS_BLOCKED_COST_LIMIT = "blocked_cost_limit"
AI_STATUS_BLOCKED_DAILY_BUDGET = "blocked_daily_budget"
AI_STATUS_BLOCKED_INPUT_TOKEN_LIMIT = "blocked_input_token_limit"
AI_STATUS_TIMEOUT = "timeout"
AI_STATUS_RATE_LIMIT = "rate_limit"
AI_STATUS_NETWORK_ERROR = "network_error"
AI_STATUS_API_ERROR = "api_error"
AI_STATUS_EMPTY_RESPONSE = "empty_response"
AI_STATUS_INVALID_JSON = "invalid_json"
AI_STATUS_SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
AI_STATUS_INCONSISTENT_WITH_ENGINE = "inconsistent_with_engine"
AI_STATUS_REPAIR_FAILED = "repair_failed"
AI_STATUS_RULE_BASED_FALLBACK = "rule_based_fallback"

# Statuses that mean "AI was disabled or budget-blocked before any request
# was ever sent" — no attempt row exists at all for these.
PRE_FLIGHT_STATUSES = frozenset(
    {AI_STATUS_DISABLED, AI_STATUS_BLOCKED_COST_LIMIT, AI_STATUS_BLOCKED_DAILY_BUDGET, AI_STATUS_BLOCKED_INPUT_TOKEN_LIMIT}
)

# Statuses reachable only after a response with real usage data came back.
USAGE_BEARING_STATUSES = frozenset(
    {
        AI_STATUS_SUCCESS,
        AI_STATUS_EMPTY_RESPONSE,
        AI_STATUS_INVALID_JSON,
        AI_STATUS_SCHEMA_VALIDATION_FAILED,
        AI_STATUS_INCONSISTENT_WITH_ENGINE,
    }
)


@dataclass(frozen=True)
class ModelAttempt:
    """One single call attempt (main, repair, or fallback-model escalation)
    — or a *considered-but-never-sent* attempt, when a pre-attempt budget
    check blocked it (`actual_model=None` in that case, distinguishing
    "we never asked OpenAI" from "OpenAI answered and it was empty/costed
    zero tokens")."""

    attempt_number: int
    is_repair: bool
    requested_model: str
    actual_model: str | None  # None if the call was never actually sent
    status: str
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    actual_cost_usd: float | None
    duration_ms: int
    error_detail: str | None  # short, redacted — never a raw prompt or API secret

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def as_dict(self) -> dict:
        return {
            "attempt_number": self.attempt_number,
            "is_repair": self.is_repair,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "status": self.status,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "actual_cost_usd": self.actual_cost_usd,
            "duration_ms": self.duration_ms,
            "error_detail": self.error_detail,
        }


def sum_optional(values: list[int | float | None]) -> int | float | None:
    """Sums the non-`None` values; returns `None` (not `0`) if every value
    is `None` — the whole point being that "no attempt ever reported usage"
    must stay distinguishable from "usage was reported and happened to be
    zero"."""
    real = [v for v in values if v is not None]
    if not real:
        return None
    return sum(real)
