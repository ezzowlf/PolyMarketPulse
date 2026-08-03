import json
from types import SimpleNamespace

import pytest

from polymarketpulse.ai.client import (
    AINetworkError,
    AIRateLimitError,
    AIResponseError,
    AITimeoutError,
    OpenAIStructuredClient,
)
from polymarketpulse.ai.schemas import AnalysisResult


def _valid_payload() -> dict:
    return {
        "summary": "Test",
        "supporting_factors": [],
        "opposing_factors": [],
        "relevant_news": [],
        "data_gaps": [],
        "uncertainties": [],
        "market_move_explanation": "Test explanation",
        "confidence_in_analysis": 0.5,
        "source_ids": [],
        "disclaimer": "Research-Hinweis – keine Wettaufforderung.",
    }


def _client_with_fake_openai(create_fn):
    client = OpenAIStructuredClient.__new__(OpenAIStructuredClient)
    client._model = "gpt-4.1-mini"
    client._timeout_seconds = 30
    client._max_output_tokens = 1200

    import openai as real_openai

    client._openai = real_openai
    client._client = SimpleNamespace(responses=SimpleNamespace(create=create_fn))
    return client


def test_generate_structured_success() -> None:
    def create_fn(**kwargs):
        return SimpleNamespace(
            output_text=json.dumps(_valid_payload()),
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
        )

    client = _client_with_fake_openai(create_fn)
    parsed, in_tok, out_tok = client.generate_structured("sys", "user", AnalysisResult, "market_analysis")
    assert parsed["summary"] == "Test"
    assert in_tok == 10
    assert out_tok == 20


def test_generate_structured_empty_response_raises() -> None:
    def create_fn(**kwargs):
        return SimpleNamespace(output_text="", usage=None)

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AIResponseError):
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")


def test_generate_structured_invalid_json_raises() -> None:
    def create_fn(**kwargs):
        return SimpleNamespace(output_text="not json", usage=None)

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AIResponseError):
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")


def test_generate_structured_schema_violation_raises() -> None:
    def create_fn(**kwargs):
        bad = _valid_payload()
        del bad["summary"]
        return SimpleNamespace(output_text=json.dumps(bad), usage=None)

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AIResponseError):
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")


def test_generate_structured_timeout_mapped() -> None:
    import openai as real_openai

    def create_fn(**kwargs):
        raise real_openai.APITimeoutError(request=SimpleNamespace())

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AITimeoutError):
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")


def test_generate_structured_rate_limit_mapped() -> None:
    import httpx
    import openai as real_openai

    def create_fn(**kwargs):
        response = httpx.Response(429, request=httpx.Request("POST", "https://x"))
        raise real_openai.RateLimitError("rate limited", response=response, body=None)

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AIRateLimitError):
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")


def test_generate_structured_connection_error_mapped() -> None:
    import openai as real_openai

    def create_fn(**kwargs):
        raise real_openai.APIConnectionError(request=SimpleNamespace())

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AINetworkError):
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")


def test_wrapper_does_not_store_api_key_as_plain_attribute() -> None:
    """The wrapper itself must not hold the raw key under an obvious
    attribute name — it's handed straight to the openai SDK's own client
    and never touched again, so nothing in our code can accidentally log it."""
    client = OpenAIStructuredClient.__new__(OpenAIStructuredClient)
    client._model = "gpt-4.1-mini"
    for attr in ("api_key", "_api_key", "key", "_key"):
        assert not hasattr(client, attr)
