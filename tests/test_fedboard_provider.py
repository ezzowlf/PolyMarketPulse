from datetime import date

from polymarketpulse.providers.fedboard import _parse_decision, _statement_urls


def test_statement_urls_selects_latest_past_official_statement() -> None:
    html = '''<a href="/newsevents/pressreleases/monetary20260617a.htm">June</a>
    <a href="/newsevents/pressreleases/monetary20260729a.htm">July</a>
    <a href="/newsevents/pressreleases/monetary20260916a.htm">September</a>'''
    assert _statement_urls(html, date(2026, 8, 14)) == [
        (date(2026, 6, 17), "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm"),
        (date(2026, 7, 29), "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm"),
    ]


def test_statement_parser_normalizes_hold_and_target_range() -> None:
    decision = _parse_decision(
        "<p>The Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent.</p>",
        date(2026, 7, 29), "https://fed.example/july", "2026-08-14T00:00:00+00:00",
    )
    assert decision is not None
    assert decision.action == "UNCHANGED"
    assert (decision.target_lower, decision.target_upper) == (3.5, 3.75)
