import json
from types import SimpleNamespace

import pytest

from polymarketpulse.ai.client import (
    AINetworkError,
    AIRateLimitError,
    AIResponseError,
    AITimeoutError,
    OpenAIStructuredClient,
    _strictify_schema,
)
from polymarketpulse.ai.schemas import AnalysisResult, MarketContext, SupportingFactor


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
    client._reasoning_effort = None

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


def _message_item(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=text)])


def _reasoning_item() -> SimpleNamespace:
    return SimpleNamespace(type="reasoning", content=[])


def test_generate_structured_falls_back_to_output_array_when_output_text_empty() -> None:
    """`output_text` empty but a real message item exists in `output` — the
    exact gap a live smoke test found (empty_response despite real usage)."""
    def create_fn(**kwargs):
        return SimpleNamespace(
            output_text="",
            output=[_message_item(json.dumps(_valid_payload()))],
            usage=SimpleNamespace(input_tokens=50, output_tokens=30),
            status="completed",
            incomplete_details=None,
        )

    client = _client_with_fake_openai(create_fn)
    parsed, in_tok, out_tok = client.generate_structured("sys", "user", AnalysisResult, "market_analysis")
    assert parsed["summary"] == "Test"
    assert in_tok == 50
    assert out_tok == 30


def test_generate_structured_reasoning_item_plus_final_text() -> None:
    def create_fn(**kwargs):
        return SimpleNamespace(
            output_text="",
            output=[_reasoning_item(), _message_item(json.dumps(_valid_payload()))],
            usage=SimpleNamespace(input_tokens=80, output_tokens=60),
            status="completed",
            incomplete_details=None,
        )

    client = _client_with_fake_openai(create_fn)
    parsed, _in_tok, _out_tok = client.generate_structured("sys", "user", AnalysisResult, "market_analysis")
    assert parsed["summary"] == "Test"


def test_generate_structured_multiple_output_items_concatenated() -> None:
    payload_text = json.dumps(_valid_payload())
    def create_fn(**kwargs):
        return SimpleNamespace(
            output_text="",
            output=[_message_item(payload_text[: len(payload_text) // 2]), _message_item(payload_text[len(payload_text) // 2 :])],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            status="completed",
            incomplete_details=None,
        )

    client = _client_with_fake_openai(create_fn)
    parsed, _, _ = client.generate_structured("sys", "user", AnalysisResult, "market_analysis")
    assert parsed["summary"] == "Test"


def test_generate_structured_empty_despite_usage_raises_with_diagnostics() -> None:
    """Reasoning-only output, no message item at all — a real empty
    response, correctly still raised, but now with diagnostics in the
    message (status/incomplete_reason/item counts) instead of a bare
    'Empty response'."""
    def create_fn(**kwargs):
        return SimpleNamespace(
            output_text="",
            output=[_reasoning_item()],
            usage=SimpleNamespace(input_tokens=40, output_tokens=800),
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AIResponseError) as exc_info:
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")
    assert "max_output_tokens" in str(exc_info.value)
    assert exc_info.value.input_tokens == 40
    assert exc_info.value.output_tokens == 800


def test_generate_structured_unknown_incomplete_reason() -> None:
    def create_fn(**kwargs):
        return SimpleNamespace(
            output_text="",
            output=[],
            usage=SimpleNamespace(input_tokens=5, output_tokens=5),
            status="incomplete",
            incomplete_details=SimpleNamespace(reason=None),
        )

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AIResponseError):
        client.generate_structured("sys", "user", AnalysisResult, "market_analysis")


def test_error_message_never_contains_full_response_text() -> None:
    secret_looking_text = "SENSITIVE_PROMPT_ECHO_MARKER"
    def create_fn(**kwargs):
        return SimpleNamespace(
            output_text="",
            output=[_reasoning_item()],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        )

    client = _client_with_fake_openai(create_fn)
    with pytest.raises(AIResponseError) as exc_info:
        client.generate_structured("sys", secret_looking_text, AnalysisResult, "market_analysis")
    assert secret_looking_text not in str(exc_info.value)


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


def _walk_object_schemas(node):
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield node
        for value in node.values():
            yield from _walk_object_schemas(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_object_schemas(item)


def test_strictify_schema_sets_additional_properties_false_everywhere() -> None:
    schema = _strictify_schema(AnalysisResult.model_json_schema())
    object_schemas = list(_walk_object_schemas(schema))
    assert object_schemas, "expected at least one object schema (top-level + nested)"
    for obj in object_schemas:
        assert obj["additionalProperties"] is False


def test_strictify_schema_marks_every_property_required() -> None:
    schema = _strictify_schema(AnalysisResult.model_json_schema())
    for obj in _walk_object_schemas(schema):
        assert set(obj["properties"].keys()) == set(obj["required"])


def test_strictify_schema_covers_nested_supporting_factor() -> None:
    """This is the exact regression this fixed: SupportingFactor is a nested
    model referenced via $defs, and without recursing into $defs its object
    schema was missing additionalProperties:false, which OpenAI's strict
    Structured Outputs mode rejects with a 400."""
    schema = _strictify_schema(AnalysisResult.model_json_schema())
    nested = schema["$defs"]["SupportingFactor"]
    assert nested["additionalProperties"] is False
    assert set(nested["properties"].keys()) == set(nested["required"])


def test_strictify_schema_works_for_market_context_too() -> None:
    schema = _strictify_schema(MarketContext.model_json_schema())
    for obj in _walk_object_schemas(schema):
        assert obj["additionalProperties"] is False


def test_supporting_factor_forbids_extra_fields_on_the_python_side() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        SupportingFactor(factor="x", evidence="y", strength="low", unexpected="z")
