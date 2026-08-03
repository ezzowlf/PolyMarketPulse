import httpx
import pytest

from polymarketpulse.providers.base import (
    ProviderConnectionError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from polymarketpulse.providers.polymarket import PolymarketProvider, parse_market


def test_parse_market_maps_yes_no_prices() -> None:
    market = parse_market(
        {
            "id": "123",
            "conditionId": "0xabc",
            "question": "Will it happen?",
            "slug": "will-it-happen",
            "endDate": "2027-01-01T00:00:00Z",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.42", "0.58"]',
            "clobTokenIds": '["111", "222"]',
            "liquidityNum": 50000,
            "volume24hr": 12000,
            "volumeNum": 100000,
            "spread": 0.02,
            "bestBid": 0.41,
            "bestAsk": 0.43,
        }
    )
    assert market.provider == "polymarket"
    assert market.yes_price == 0.42
    assert market.no_price == 0.58
    assert market.yes_token_id == "111"
    assert market.no_token_id == "222"
    assert market.best_bid == 0.41
    assert market.best_ask == 0.43
    assert market.url.endswith("will-it-happen")
    assert not market.missing_fields
    assert market.raw_data_hash is not None


def test_parse_market_handles_missing_fields_without_crashing() -> None:
    market = parse_market({"id": "1", "slug": "bare"})
    assert market.yes_price is None
    assert market.liquidity == 0.0
    assert market.yes_token_id is None
    assert "liquidityNum" in market.missing_fields
    assert "outcomePrices" in market.missing_fields


def test_parse_market_handles_malformed_prices() -> None:
    market = parse_market(
        {
            "id": "2",
            "slug": "malformed",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": "not-json",
            "liquidityNum": "not-a-number",
        }
    )
    assert market.yes_price is None
    assert market.liquidity == 0.0


def test_parse_market_detects_resolution() -> None:
    market = parse_market(
        {
            "id": "3",
            "slug": "resolved-one",
            "closed": True,
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["1", "0"]',
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    )
    assert market.resolution_status.value == "resolved"
    assert market.winning_outcome == "Yes"
    assert market.resolved_at is not None


def test_fetch_markets_paginates_via_cursor() -> None:
    provider = PolymarketProvider()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        cursor = request.url.params.get("cursor")
        if not cursor:
            return httpx.Response(
                200,
                json={
                    "markets": [{"id": "1", "slug": "a"}, {"id": "2", "slug": "b"}],
                    "next_cursor": "page2",
                },
            )
        return httpx.Response(200, json={"markets": [{"id": "3", "slug": "c"}], "next_cursor": None})

    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    page = provider.fetch_markets(limit=3, page_size=2)
    assert len(page.items) == 3
    assert len(calls) == 2
    provider.close()


def test_fetch_markets_wraps_connection_error() -> None:
    provider = PolymarketProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderConnectionError):
        provider.fetch_markets(limit=5)
    provider.close()


def test_fetch_markets_wraps_timeout() -> None:
    provider = PolymarketProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("boom", request=request)

    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderTimeoutError):
        provider.fetch_markets(limit=5)
    provider.close()


def test_fetch_markets_rejects_invalid_json() -> None:
    provider = PolymarketProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderResponseError):
        provider.fetch_markets(limit=5)
    provider.close()


def test_fetch_markets_rejects_unexpected_shape() -> None:
    provider = PolymarketProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderResponseError):
        provider.fetch_markets(limit=5)
    provider.close()
