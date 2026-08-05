from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError


class AIError(RuntimeError):
    """Base class for all AI-layer failures. Never carries the API key or
    raw request/response payloads in its message — only what's safe to log.

    `error_code` is a stable, machine-readable identifier (see
    ai/status.py's AI_STATUS_* constants) used to persist *why* an attempt
    failed, instead of collapsing every failure into a single generic
    outcome. `input_tokens`/`output_tokens` are attached whenever OpenAI
    actually returned a response with usage data before the failure
    occurred (e.g. invalid JSON, schema mismatch) — `None` means no
    response/usage was ever received (e.g. timeout, network error), which
    must never be confused with "zero tokens were used"."""

    error_code = "api_error"

    def __init__(self, message: str, input_tokens: int | None = None, output_tokens: int | None = None) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class AIDisabledError(AIError):
    """AI is disabled or no API key is configured."""

    error_code = "disabled"


class AIContextError(AIError):
    """Not enough stored data exists to build a meaningful context."""

    error_code = "context_error"


class AITimeoutError(AIError):
    error_code = "timeout"


class AIRateLimitError(AIError):
    error_code = "rate_limit"


class AINetworkError(AIError):
    error_code = "network_error"


class AIResponseError(AIError):
    """The model's output didn't parse or didn't satisfy the schema. Base
    class for the three more specific response-stage failures below — code
    that only needs "something about the response was wrong" can still
    catch this base class."""

    error_code = "api_error"


class AIEmptyResponseError(AIResponseError):
    error_code = "empty_response"


class AIInvalidJSONError(AIResponseError):
    error_code = "invalid_json"


class AISchemaValidationError(AIResponseError):
    error_code = "schema_validation_failed"


def _redact(text: str, limit: int = 200) -> str:
    """Never let a raw upstream error message (which could echo request
    content) escape further than a short, truncated summary."""
    return text[:limit].replace("\n", " ")


def _strictify_schema(node: Any) -> Any:
    """Recursively enforce OpenAI Structured Outputs' strict-mode
    requirements: every object needs `additionalProperties: false` and
    every one of its properties listed in `required` (fields stay
    "optional" in spirit via nullable types, not via omission from
    `required` — Pydantic's own validation still enforces our real
    defaults on the Python side after parsing)."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for value in node.values():
            _strictify_schema(value)
    elif isinstance(node, list):
        for item in node:
            _strictify_schema(item)
    return node


class OpenAIStructuredClient:
    """Thin wrapper around the OpenAI Responses API for one purpose:
    produce a JSON object that validates against a given Pydantic schema.
    All OpenAI SDK exceptions are caught here and re-raised as our own
    typed errors so callers never need to import the `openai` package."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float, max_output_tokens: int) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - openai is a declared dependency
            raise AIError("openai package not installed") from exc
        self._openai = openai
        self._client = openai.OpenAI(api_key=api_key, timeout=timeout_seconds)

    def generate_structured(
        self, system_prompt: str, user_prompt: str, schema_model: type[BaseModel], schema_name: str
    ) -> tuple[dict[str, Any], int | None, int | None]:
        """Returns (parsed_json, input_tokens, output_tokens).

        Every raised exception below `response = self._client.responses...`
        carries whatever usage data OpenAI actually returned, even though
        the response itself is being rejected — callers must be able to
        persist real token/cost data for a *failed* attempt, not just a
        successful one."""
        schema = _strictify_schema(schema_model.model_json_schema())
        try:
            response = self._client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_output_tokens=self._max_output_tokens,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
            )
        except self._openai.APITimeoutError as exc:
            raise AITimeoutError("OpenAI request timed out") from exc
        except self._openai.RateLimitError as exc:
            raise AIRateLimitError("OpenAI rate limit exceeded") from exc
        except self._openai.APIConnectionError as exc:
            raise AINetworkError("Could not reach OpenAI") from exc
        except self._openai.APIError as exc:
            raise AIResponseError(f"OpenAI API error: {_redact(str(exc))}") from exc

        # Usage is available on the response object regardless of what the
        # generated text turned out to contain — extract it once, up front,
        # so every failure branch below can attach the real numbers.
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage else None
        output_tokens = getattr(usage, "output_tokens", None) if usage else None

        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise AIEmptyResponseError("Empty response from OpenAI", input_tokens, output_tokens)

        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise AIInvalidJSONError("Response was not valid JSON", input_tokens, output_tokens) from exc

        try:
            schema_model.model_validate(parsed)
        except ValidationError as exc:
            raise AISchemaValidationError(
                f"Response did not match schema: {_redact(str(exc))}", input_tokens, output_tokens
            ) from exc

        return parsed, input_tokens, output_tokens
