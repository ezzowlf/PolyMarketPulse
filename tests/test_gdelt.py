from unittest.mock import MagicMock, patch

import httpx

from polymarketpulse.news.gdelt import build_query_for_question, fetch_gdelt


def test_build_query_for_question_extracts_significant_terms() -> None:
    query = build_query_for_question("Will the ceasefire agreement be signed by Friday?")
    assert "ceasefire" in query
    assert "agreement" in query


def test_build_query_for_empty_question_returns_empty_string() -> None:
    assert build_query_for_question("") == ""


def test_build_query_phrase_quotes_multiword_entity() -> None:
    """Audit Part 1 regression: a multi-word entity like "Strait of
    Hormuz" must be phrase-quoted as a single term, not split into 3
    independent OR terms (the real over-broad-query bug found in the
    audit: "strait OR hormuz OR traffic OR returns OR normal OR august")."""
    query = build_query_for_question("Will the Strait of Hormuz traffic return to normal by August 31?")
    assert '"Strait of Hormuz"' in query
    # The old behavior (pure keyword-OR, no phrase-quoting) would have
    # produced independent "strait" / "hormuz" terms instead.
    assert " OR strait " not in f" {query} "
    assert " OR hormuz " not in f" {query} "


def test_build_query_expanded_function_word_list_excludes_real_non_substantive_words() -> None:
    """Audit Part 1 regression: words like "before"/"next"/"state" are
    length>3 and used to slip through the old length<=3-only stopword
    filter. They must not appear as bare OR terms now."""
    query = build_query_for_question("Will the state announce a decision before the next meeting?")
    assert "state" not in query.split(" OR ")
    assert "before" not in query.split(" OR ")
    assert "next" not in query.split(" OR ")


def test_build_query_more_specific_than_old_flat_keyword_or_for_real_market() -> None:
    """Before/after comparison for a real acceptance-run market question:
    the new query must be strictly more specific (contains a quoted
    phrase) than the old flat OR-of-words query would have been."""
    question = "Will the President of the United States be out of office by August 31?"
    old_style_terms = [
        w.strip(".,?!\"'()").lower()
        for w in question.split()
        if len(w.strip(".,?!\"'()")) > 3
        and w.strip(".,?!\"'()").lower() not in {"the", "will"}
    ]
    old_query = " OR ".join(old_style_terms[:6])
    new_query = build_query_for_question(question)
    assert '"' in new_query  # phrase-quoted — old query never quotes anything
    assert new_query != old_query


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
