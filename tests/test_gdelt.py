from unittest.mock import MagicMock, patch

import httpx

from polymarketpulse.news.gdelt import build_query_for_question, fetch_gdelt


def test_build_query_for_question_extracts_significant_terms() -> None:
    query = build_query_for_question("Will the ceasefire agreement be signed by Friday?")
    assert "ceasefire" in query
    assert "agreement" in query


def test_build_query_for_empty_question_returns_empty_string() -> None:
    assert build_query_for_question("") == ""


def test_fetch_gdelt_parses_articles(monkeypatch) -> None:
    # assert_safe_url does a real DNS lookup by design (SSRF guard) — stubbed
    # here so this stays a pure, network-free unit test.
    monkeypatch.setattr("polymarketpulse.news.gdelt.assert_safe_url", lambda url: None)
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "articles": [
            {
                "title": "Ceasefire confirmed by officials",
                "url": "https://reuters.com/article1",
                "domain": "reuters.com",
                "seendate": "20260801T120000Z",
                "tone": "3.5",
            }
        ]
    }
    with patch("polymarketpulse.news.gdelt.httpx.get", return_value=mock_response) as mock_get:
        events = fetch_gdelt("ceasefire")
    assert mock_get.called
    assert len(events) == 1
    assert events[0].title == "Ceasefire confirmed by officials"
    assert events[0].tone == 3.5
    assert events[0].source_domain == "reuters.com"


def test_fetch_gdelt_returns_empty_on_error(monkeypatch) -> None:
    monkeypatch.setattr("polymarketpulse.news.gdelt.assert_safe_url", lambda url: None)
    with patch("polymarketpulse.news.gdelt.httpx.get", side_effect=httpx.ConnectError("boom")):
        events = fetch_gdelt("ceasefire")
    assert events == []


def test_fetch_gdelt_empty_query_returns_empty_without_network_call() -> None:
    with patch("polymarketpulse.news.gdelt.httpx.get") as mock_get:
        events = fetch_gdelt("")
    assert events == []
    assert not mock_get.called
