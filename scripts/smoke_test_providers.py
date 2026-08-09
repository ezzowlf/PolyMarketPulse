"""Standalone, portable live-provider connectivity smoke test.

Run this OUTSIDE the sandbox, on your own normal machine, with:

    python scripts/smoke_test_providers.py

It makes real outbound HTTPS requests to FRED, CoinGecko, GDELT, and the
project's default RSS feeds, and prints a clear per-provider result: the
real HTTP status on success, or the exact TLS/HTTP/parsing error on
failure. No mocking, no fabricated results.

It deliberately reuses `polymarketpulse.security.get_ssl_context()` — the
SAME CA-bundle logic the real application uses for every outbound
request — instead of a separate, divergent implementation. That means:

  - On a normal machine with no TLS-interception AV, this uses plain
    certifi and behaves exactly like any other Python HTTPS client.
  - On a machine where an env var such as NODE_EXTRA_CA_CERTS,
    SSL_CERT_FILE, or REQUESTS_CA_BUNDLE points at an extra, locally
    trusted root CA (e.g. installed by antivirus software doing TLS
    inspection), this script automatically picks that up too — same as
    the real app would.

Nothing here disables certificate verification, ever.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly from a checkout without `pip install -e .` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from polymarketpulse.security import get_ca_bundle, get_ssl_context


def _check(name: str, url: str, params: dict | None = None) -> None:
    print(f"\n[{name}] GET {url}")
    try:
        response = httpx.get(
            url,
            params=params,
            timeout=15.0,
            headers={"User-Agent": "PolymarketPulse-SmokeTest/0.1"},
            verify=get_ssl_context(),
        )
        preview = response.text[:120].replace("\n", " ")
        print(f"  -> HTTP {response.status_code}  body preview: {preview!r}")
        if response.status_code == 200:
            print("  RESULT: OK (real 200)")
        elif response.status_code == 429:
            print("  RESULT: reached the server, but rate-limited (429) — connectivity is fine")
        else:
            print(f"  RESULT: reached the server but got a non-200 status ({response.status_code})")
    except httpx.ConnectError as exc:
        print(f"  RESULT: CONNECTION FAILED: {exc!r}")
    except httpx.TimeoutException as exc:
        print(f"  RESULT: TIMED OUT: {exc!r}")
    except Exception as exc:  # noqa: BLE001 - smoke test wants to show any real error, not swallow it
        print(f"  RESULT: ERROR ({type(exc).__name__}): {exc!r}")


def main() -> None:
    print("PolymarketPulse provider connectivity smoke test")
    print(f"CA bundle in use: {get_ca_bundle()}")
    print("(This is plain certifi unless NODE_EXTRA_CA_CERTS / SSL_CERT_FILE / "
          "REQUESTS_CA_BUNDLE is set to a real, readable file on this machine.)")

    _check(
        "FRED",
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": "FEDFUNDS"},
    )
    _check(
        "CoinGecko",
        "https://api.coingecko.com/api/v3/ping",
    )
    _check(
        "GDELT",
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={"query": "test", "mode": "artlist", "format": "json"},
    )

    # Same default RSS feeds the app ships with (news/rss.py::DEFAULT_FEEDS),
    # duplicated here rather than imported so this script has no dependency
    # on internal news-module internals — just the CA helper.
    rss_feeds = {
        "federal_reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
        "ecb": "https://www.ecb.europa.eu/rss/press.html",
        "whitehouse": "https://www.whitehouse.gov/news/feed/",
        "un_news": "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
        "state_department": "https://www.state.gov/rss-feed/press-releases/feed/",
        "sec": "https://www.sec.gov/news/pressreleases.rss",
    }
    for source_name, feed_url in rss_feeds.items():
        _check(f"RSS:{source_name}", feed_url)

    print("\nDone. Every result above reflects a real outbound attempt just now — "
          "nothing here is cached or fabricated.")


if __name__ == "__main__":
    main()
