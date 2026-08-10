"""One-command live TLS/connectivity/parse smoke for every real public provider."""

from __future__ import annotations

import csv
import io
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx

from polymarketpulse.security import get_ssl_context, get_tls_trust_source


@dataclass(frozen=True)
class Check:
    name: str
    url: str
    parser: Callable[[httpx.Response], int]
    params: dict[str, str] | None = None


def _json_list(response: httpx.Response) -> int:
    payload = response.json()
    if not isinstance(payload, list):
        raise TypeError("expected JSON list")
    return len(payload)


def _json_object(response: httpx.Response) -> int:
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("expected JSON object")
    return len(payload)


def _json_value(response: httpx.Response) -> int:
    response.json()
    return 1


def _gamma(response: httpx.Response) -> int:
    payload = response.json()
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("markets"), list):
        return len(payload["markets"])
    raise ValueError("unexpected Gamma response")


def _fred_csv(response: httpx.Response) -> int:
    rows = list(csv.reader(io.StringIO(response.text)))
    if len(rows) < 2 or rows[0][:2] != ["DATE", "FEDFUNDS"]:
        raise ValueError("unexpected FRED CSV")
    return len(rows) - 1


def _rss(response: httpx.Response) -> int:
    root = ET.fromstring(response.text.lstrip("\ufeff"))
    items = root.findall(".//item")
    if items:
        return len(items)
    atom = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if atom:
        return len(atom)
    raise ValueError("valid XML but no RSS/Atom entries")


def _run(check: Check) -> dict:
    started = time.perf_counter()
    try:
        response = httpx.get(
            check.url,
            params=check.params,
            timeout=20.0,
            headers={"User-Agent": "PolymarketPulse-SmokeTest/1.0"},
            verify=get_ssl_context(),
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.status_code == 429:
            return {
                "provider": check.name, "connectivity": "OK", "tls": "OK",
                "http": 429, "parse": "SKIPPED", "items": 0,
                "latency_ms": latency_ms, "status": "DEGRADED_RATE_LIMITED",
                "retry_after": response.headers.get("Retry-After"),
            }
        if response.status_code != 200:
            return {
                "provider": check.name, "connectivity": "OK", "tls": "OK",
                "http": response.status_code, "parse": "SKIPPED", "items": 0,
                "latency_ms": latency_ms, "status": "FAILED_HTTP",
            }
        try:
            items = check.parser(response)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, ET.ParseError) as exc:
            return {
                "provider": check.name, "connectivity": "OK", "tls": "OK",
                "http": 200, "parse": f"FAILED:{type(exc).__name__}", "items": 0,
                "latency_ms": latency_ms, "status": "FAILED_PARSE",
            }
        return {
            "provider": check.name, "connectivity": "OK", "tls": "OK",
            "http": 200, "parse": "OK", "items": items,
            "latency_ms": latency_ms, "status": "LIVE",
        }
    except httpx.TimeoutException:
        error = "TIMEOUT"
    except httpx.ConnectError as exc:
        error = "TLS_FAILED" if "CERTIFICATE_VERIFY_FAILED" in str(exc) else "CONNECTION_FAILED"
    except httpx.RemoteProtocolError:
        error = "REMOTE_PROTOCOL_ERROR"
    return {
        "provider": check.name, "connectivity": "FAILED", "tls": "UNKNOWN",
        "http": None, "parse": "SKIPPED", "items": 0,
        "latency_ms": round((time.perf_counter() - started) * 1000), "status": error,
    }


CHECKS = (
    Check("Polymarket Gamma", "https://gamma-api.polymarket.com/markets", _json_list, {"limit": "1"}),
    Check("Polymarket CLOB", "https://clob.polymarket.com/time", _json_value),
    Check(
        "Polymarket Data", "https://data-api.polymarket.com/positions", _json_list,
        {"user": "0x0000000000000000000000000000000000000000", "limit": "1"},
    ),
    Check("FRED", "https://fred.stlouisfed.org/graph/fredgraph.csv", _fred_csv, {"id": "FEDFUNDS"}),
    Check("CoinGecko", "https://api.coingecko.com/api/v3/ping", _json_object),
    Check(
        "GDELT", "https://api.gdeltproject.org/api/v2/doc/doc", _json_object,
        {"query": "test", "mode": "artlist", "format": "json"},
    ),
    Check("Federal Reserve RSS", "https://www.federalreserve.gov/feeds/press_all.xml", _rss),
    Check("ECB RSS", "https://www.ecb.europa.eu/rss/press.html", _rss),
    Check("White House RSS", "https://www.whitehouse.gov/news/feed/", _rss),
    Check("UN News RSS", "https://news.un.org/feed/subscribe/en/news/all/rss.xml", _rss),
    Check("State Department RSS", "https://www.state.gov/rss-feed/press-releases/feed/", _rss),
    Check("SEC RSS", "https://www.sec.gov/news/pressreleases.rss", _rss),
    Check("Manifold", "https://api.manifold.markets/v0/markets", _json_list, {"limit": "1"}),
    Check("PredictIt", "https://www.predictit.org/api/marketdata/all/", _json_object),
)


def main() -> None:
    print(f"TLS trust: {get_tls_trust_source()}")
    print("Provider | Connectivity | TLS | HTTP | Parse | Items | Latency ms | Status")
    for check in CHECKS:
        result = _run(check)
        print(
            f"{result['provider']} | {result['connectivity']} | {result['tls']} | "
            f"{result['http'] or '-'} | {result['parse']} | {result['items']} | "
            f"{result['latency_ms']} | {result['status']}"
        )


if __name__ == "__main__":
    main()
