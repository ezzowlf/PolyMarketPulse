"""Public Polymarket market-flow data — order book, trades, and token
holders. All three endpoints are documented, unauthenticated, publicly
accessible REST APIs (the same ones polymarket.com's own UI calls) —
no login, no scraping behind access control, no private data. Wallet
addresses returned here are public on-chain addresses; nothing here
attempts to map them to real identities.

Fixed, hardcoded hostnames only (no user-controlled URL construction),
still routed through the shared SSRF guard for defense in depth, plus a
response-size cap, timeout, and simple retry-with-backoff on transient
failures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from ..security import MAX_RESPONSE_BYTES, SSRFError, assert_safe_url

CLOB_BOOK_URL = "https://clob.polymarket.com/book"
DATA_API_TRADES_URL = "https://data-api.polymarket.com/trades"
DATA_API_HOLDERS_URL = "https://data-api.polymarket.com/holders"

DEFAULT_TIMEOUT = 10.0
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBookResult:
    fetched: bool
    token_id: str
    bids: tuple[BookLevel, ...] = field(default_factory=tuple)
    asks: tuple[BookLevel, ...] = field(default_factory=tuple)
    error: str | None = None


@dataclass(frozen=True)
class PublicTrade:
    trade_hash: str
    wallet_address: str
    side: str
    price: float
    size: float
    traded_at_unix: int
    outcome: str | None = None


@dataclass(frozen=True)
class TradesResult:
    fetched: bool
    trades: tuple[PublicTrade, ...] = field(default_factory=tuple)
    error: str | None = None


@dataclass(frozen=True)
class HolderEntry:
    wallet_address: str
    amount: float
    outcome_index: int | None = None


@dataclass(frozen=True)
class HoldersResult:
    fetched: bool
    holders: tuple[HolderEntry, ...] = field(default_factory=tuple)
    error: str | None = None


def _get_with_retry(url: str, params: dict, timeout: float) -> httpx.Response | None:
    try:
        assert_safe_url(url)
    except SSRFError:
        return None
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = httpx.get(url, params=params, timeout=timeout, headers={"User-Agent": "PolymarketPulse/0.2"})
            if response.status_code == 429 or response.status_code >= 500:
                last_exc = httpx.HTTPStatusError("retryable status", request=response.request, response=response)
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                return None
            return response
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    del last_exc
    return None


def fetch_order_book(token_id: str, timeout: float = DEFAULT_TIMEOUT) -> OrderBookResult:
    response = _get_with_retry(CLOB_BOOK_URL, {"token_id": token_id}, timeout)
    if response is None:
        return OrderBookResult(fetched=False, token_id=token_id, error="Anfrage fehlgeschlagen oder Rate-Limit erreicht.")
    try:
        payload = response.json()
    except ValueError:
        return OrderBookResult(fetched=False, token_id=token_id, error="Antwort nicht als JSON lesbar.")

    def _levels(raw: list) -> tuple[BookLevel, ...]:
        levels = []
        for entry in raw or []:
            try:
                levels.append(BookLevel(price=float(entry["price"]), size=float(entry["size"])))
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(levels)

    return OrderBookResult(
        fetched=True, token_id=token_id,
        bids=_levels(payload.get("bids")), asks=_levels(payload.get("asks")),
    )


def fetch_trades(condition_id: str, limit: int = 100, timeout: float = DEFAULT_TIMEOUT) -> TradesResult:
    response = _get_with_retry(DATA_API_TRADES_URL, {"market": condition_id, "limit": limit}, timeout)
    if response is None:
        return TradesResult(fetched=False, error="Anfrage fehlgeschlagen oder Rate-Limit erreicht.")
    try:
        payload = response.json()
    except ValueError:
        return TradesResult(fetched=False, error="Antwort nicht als JSON lesbar.")

    trades = []
    for entry in payload or []:
        try:
            trades.append(
                PublicTrade(
                    trade_hash=entry["transactionHash"], wallet_address=entry["proxyWallet"],
                    side=entry["side"], price=float(entry["price"]), size=float(entry["size"]),
                    traded_at_unix=int(entry["timestamp"]), outcome=entry.get("outcome"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return TradesResult(fetched=True, trades=tuple(trades))


def fetch_holders(condition_id: str, limit: int = 20, timeout: float = DEFAULT_TIMEOUT) -> HoldersResult:
    response = _get_with_retry(DATA_API_HOLDERS_URL, {"market": condition_id, "limit": limit}, timeout)
    if response is None:
        return HoldersResult(fetched=False, error="Anfrage fehlgeschlagen oder Rate-Limit erreicht.")
    try:
        payload = response.json()
    except ValueError:
        return HoldersResult(fetched=False, error="Antwort nicht als JSON lesbar.")

    holders = []
    for token_group in payload or []:
        for entry in token_group.get("holders", []):
            try:
                holders.append(
                    HolderEntry(
                        wallet_address=entry["proxyWallet"], amount=float(entry["amount"]),
                        outcome_index=entry.get("outcomeIndex"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return HoldersResult(fetched=True, holders=tuple(holders))
