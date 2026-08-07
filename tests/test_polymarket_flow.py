from unittest.mock import MagicMock, patch

import httpx

from polymarketpulse.providers.polymarket_flow import fetch_holders, fetch_order_book, fetch_trades


def _mock_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b"x" * 100
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_order_book_parses_bids_asks(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.assert_safe_url", lambda url: None)
    payload = {"bids": [{"price": "0.5", "size": "100"}], "asks": [{"price": "0.51", "size": "50"}]}
    with patch("polymarketpulse.providers.polymarket_flow.httpx.get", return_value=_mock_response(payload)):
        result = fetch_order_book("12345")
    assert result.fetched is True
    assert len(result.bids) == 1
    assert result.bids[0].price == 0.5


def test_fetch_order_book_returns_unfetched_on_repeated_failure(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.assert_safe_url", lambda url: None)
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.RETRY_BACKOFF_SECONDS", 0.0)
    with patch("polymarketpulse.providers.polymarket_flow.httpx.get", side_effect=httpx.ConnectError("boom")):
        result = fetch_order_book("12345")
    assert result.fetched is False


def test_fetch_trades_parses_entries(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.assert_safe_url", lambda url: None)
    payload = [
        {
            "transactionHash": "0xabc", "proxyWallet": "0xdef", "side": "BUY",
            "price": 0.5, "size": 10, "timestamp": 1700000000, "outcome": "Yes",
        }
    ]
    with patch("polymarketpulse.providers.polymarket_flow.httpx.get", return_value=_mock_response(payload)):
        result = fetch_trades("0xcondition")
    assert result.fetched is True
    assert len(result.trades) == 1
    assert result.trades[0].wallet_address == "0xdef"


def test_fetch_holders_parses_nested_structure(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.assert_safe_url", lambda url: None)
    payload = [
        {"token": "t1", "holders": [{"proxyWallet": "0xaaa", "amount": 500, "outcomeIndex": 0}]}
    ]
    with patch("polymarketpulse.providers.polymarket_flow.httpx.get", return_value=_mock_response(payload)):
        result = fetch_holders("0xcondition")
    assert result.fetched is True
    assert len(result.holders) == 1
    assert result.holders[0].amount == 500


def test_oversized_response_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.assert_safe_url", lambda url: None)
    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.MAX_RESPONSE_BYTES", 10)
    resp = _mock_response({"bids": [], "asks": []})
    resp.content = b"x" * 1000
    with patch("polymarketpulse.providers.polymarket_flow.httpx.get", return_value=resp):
        result = fetch_order_book("12345")
    assert result.fetched is False


def test_ssrf_blocked_url_prevents_request(monkeypatch) -> None:
    from polymarketpulse.security import SSRFError

    def _raise(url):
        raise SSRFError("blocked")

    monkeypatch.setattr("polymarketpulse.providers.polymarket_flow.assert_safe_url", _raise)
    with patch("polymarketpulse.providers.polymarket_flow.httpx.get") as mock_get:
        result = fetch_order_book("12345")
    assert result.fetched is False
    assert not mock_get.called
