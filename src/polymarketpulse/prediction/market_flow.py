"""Pure metric functions over already-fetched order book / trade / holder
data (see `providers/polymarket_flow.py` for the collector). Nothing here
makes network calls — every function takes plain data in and returns a
plain, auditable result, so it can be unit tested without a network.

Every "risk"/"concentration" number here is a *descriptive* statistic, not
an accusation: see the neutral status vocabulary used throughout
(`STATUS_*` constants) rather than words like "manipulation" or "insider".
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

LARGE_TRADE_USD_THRESHOLD = 1000.0  # size*price >= this counts as a "large trade"

STATUS_NO_SIGNAL = "kein Signal"
STATUS_WATCH = "beobachten"
STATUS_UNUSUAL_FLOW = "ungewöhnlicher Flow"
STATUS_STRONG_FLOW = "starkes Flow-Signal"
STATUS_INSUFFICIENT_DATA = "Datenlage unzureichend"


@dataclass(frozen=True)
class OrderBookMetrics:
    available: bool
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    midprice: float | None = None
    bid_depth: float | None = None  # sum of bid size * price across levels
    ask_depth: float | None = None
    imbalance: float | None = None  # -1 (all ask) .. +1 (all bid)
    thin: bool = False
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available, "best_bid": self.best_bid, "best_ask": self.best_ask,
            "spread": self.spread, "midprice": self.midprice, "bid_depth": self.bid_depth,
            "ask_depth": self.ask_depth, "imbalance": self.imbalance, "thin": self.thin, "detail": self.detail,
        }


def compute_orderbook_metrics(bids: list, asks: list, depth_levels: int = 10, thin_depth_usd: float = 500.0) -> OrderBookMetrics:
    """`bids`/`asks` are iterables of objects/dicts with `.price`/`.size`
    (or `["price"]`/`["size"]`), best price first."""

    def _price(x):
        return x.price if hasattr(x, "price") else x["price"]

    def _size(x):
        return x.size if hasattr(x, "size") else x["size"]

    if not bids and not asks:
        return OrderBookMetrics(available=False, detail="Kein Orderbuch abrufbar oder leer.")

    best_bid = _price(bids[0]) if bids else None
    best_ask = _price(asks[0]) if asks else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
    midprice = ((best_ask + best_bid) / 2) if spread is not None else (best_bid or best_ask)

    bid_depth = sum(_price(b) * _size(b) for b in bids[:depth_levels])
    ask_depth = sum(_price(a) * _size(a) for a in asks[:depth_levels])
    total_depth = bid_depth + ask_depth
    imbalance = round((bid_depth - ask_depth) / total_depth, 4) if total_depth > 0 else None
    thin = total_depth < thin_depth_usd

    detail = (
        f"Spread {spread:.4f}, Tiefe (bid+ask, {depth_levels} Stufen) ${total_depth:,.0f}, "
        f"Imbalance {imbalance:+.2f}." if spread is not None else "Nur eine Orderbuchseite verfügbar."
    )
    if thin:
        detail += " Orderbuch dünn."

    return OrderBookMetrics(
        available=True, best_bid=best_bid, best_ask=best_ask, spread=round(spread, 4) if spread is not None else None,
        midprice=round(midprice, 4) if midprice is not None else None, bid_depth=round(bid_depth, 2),
        ask_depth=round(ask_depth, 2), imbalance=imbalance, thin=thin, detail=detail,
    )


@dataclass(frozen=True)
class TradeFlowMetrics:
    available: bool
    trade_count: int = 0
    buy_volume_usd: float = 0.0
    sell_volume_usd: float = 0.0
    net_flow_usd: float = 0.0
    large_trade_ratio: float | None = None  # share of volume from trades >= LARGE_TRADE_USD_THRESHOLD
    largest_trade_usd: float | None = None
    status: str = STATUS_INSUFFICIENT_DATA
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available, "trade_count": self.trade_count, "buy_volume_usd": self.buy_volume_usd,
            "sell_volume_usd": self.sell_volume_usd, "net_flow_usd": self.net_flow_usd,
            "large_trade_ratio": self.large_trade_ratio, "largest_trade_usd": self.largest_trade_usd,
            "status": self.status, "detail": self.detail,
        }


def compute_trade_flow_metrics(trades: list, large_trade_usd_threshold: float = LARGE_TRADE_USD_THRESHOLD) -> TradeFlowMetrics:
    """`trades` is an iterable of objects/dicts with `.side` ("BUY"/"SELL"),
    `.price`, `.size`."""
    if not trades:
        return TradeFlowMetrics(available=False, status=STATUS_INSUFFICIENT_DATA, detail="Keine öffentlichen Trades gefunden.")

    def _get(t, key):
        return getattr(t, key) if hasattr(t, key) else t[key]

    buy_usd = 0.0
    sell_usd = 0.0
    large_usd = 0.0
    total_usd = 0.0
    largest = 0.0
    for t in trades:
        usd = float(_get(t, "price")) * float(_get(t, "size"))
        total_usd += usd
        largest = max(largest, usd)
        side = str(_get(t, "side")).upper()
        if side == "BUY":
            buy_usd += usd
        elif side == "SELL":
            sell_usd += usd
        if usd >= large_trade_usd_threshold:
            large_usd += usd

    net_flow = buy_usd - sell_usd
    large_ratio = round(large_usd / total_usd, 4) if total_usd > 0 else None

    if len(trades) < 3:
        status = STATUS_INSUFFICIENT_DATA
    elif large_ratio is not None and large_ratio >= 0.5:
        status = STATUS_STRONG_FLOW
    elif abs(net_flow) > 0 and total_usd > 0 and abs(net_flow) / total_usd >= 0.6:
        status = STATUS_UNUSUAL_FLOW
    else:
        status = STATUS_WATCH

    direction = "Kaufdruck" if net_flow > 0 else "Verkaufsdruck" if net_flow < 0 else "ausgeglichen"
    detail = (
        f"{len(trades)} öffentliche Trade(s), Netto-Flow ${net_flow:+,.0f} ({direction}), "
        f"Anteil großer Trades (>= ${large_trade_usd_threshold:,.0f}): "
        f"{large_ratio:.0%}." if large_ratio is not None else f"{len(trades)} Trade(s)."
    )

    return TradeFlowMetrics(
        available=True, trade_count=len(trades), buy_volume_usd=round(buy_usd, 2), sell_volume_usd=round(sell_usd, 2),
        net_flow_usd=round(net_flow, 2), large_trade_ratio=large_ratio, largest_trade_usd=round(largest, 2),
        status=status, detail=detail,
    )


@dataclass(frozen=True)
class WalletConcentrationMetrics:
    available: bool
    holder_count: int = 0
    top1_share: float | None = None
    top3_share: float | None = None
    concentration_score: float | None = None  # 0..100, HHI-derived
    top_wallets: tuple[str, ...] = field(default_factory=tuple)  # truncated addresses only
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available, "holder_count": self.holder_count, "top1_share": self.top1_share,
            "top3_share": self.top3_share, "concentration_score": self.concentration_score,
            "top_wallets": list(self.top_wallets), "detail": self.detail,
        }


def _truncate_wallet(address: str) -> str:
    if len(address) <= 10:
        return address
    return f"{address[:6]}...{address[-4:]}"


def compute_wallet_concentration(holders: list) -> WalletConcentrationMetrics:
    """`holders` is an iterable of objects/dicts with `.wallet_address`/
    `.amount`. Only ever shows truncated addresses — never a full one."""
    if not holders:
        return WalletConcentrationMetrics(available=False, detail="Keine öffentlichen Positionsdaten gefunden.")

    def _get(h, key):
        return getattr(h, key) if hasattr(h, key) else h[key]

    amounts = [(str(_get(h, "wallet_address")), float(_get(h, "amount"))) for h in holders]
    amounts.sort(key=lambda x: x[1], reverse=True)
    total = sum(a for _, a in amounts)
    if total <= 0:
        return WalletConcentrationMetrics(available=False, detail="Positionsdaten ohne auswertbares Volumen.")

    shares = [a / total for _, a in amounts]
    top1_share = round(shares[0], 4)
    top3_share = round(sum(shares[:3]), 4)
    hhi = sum(s * s for s in shares) * 10000  # 0..10000, 10000 = single holder
    concentration_score = round(min(100.0, hhi / 100), 1)

    detail = f"{len(amounts)} öffentliche Adresse(n), größte Adresse hält {top1_share:.0%} der sichtbaren Position."
    return WalletConcentrationMetrics(
        available=True, holder_count=len(amounts), top1_share=top1_share, top3_share=top3_share,
        concentration_score=concentration_score, top_wallets=tuple(_truncate_wallet(a) for a, _ in amounts[:5]),
        detail=detail,
    )


def load_flow_metrics_from_db(
    conn: sqlite3.Connection, provider: str, provider_market_id: str
) -> tuple[OrderBookMetrics, TradeFlowMetrics, WalletConcentrationMetrics]:
    """Reads the most recently *collected* (by `flow-fetch`) order book,
    trades, and holder snapshots for this market and turns them into
    metrics. Does not make any network call itself — collection and
    computation are deliberately separate, same as the news/evidence
    pipeline."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    book_metrics = OrderBookMetrics(available=False, detail="Orderbuch-Infrastruktur nicht vorhanden.")
    if "orderbook_snapshots" in tables:
        row = conn.execute(
            "SELECT bids_json, asks_json FROM orderbook_snapshots WHERE provider = ? AND provider_market_id = ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (provider, provider_market_id),
        ).fetchone()
        if row is None:
            book_metrics = OrderBookMetrics(available=False, detail="Kein Orderbuch-Snapshot erfasst (flow-fetch noch nicht ausgeführt oder Markt ohne Token-ID).")
        else:
            book_metrics = compute_orderbook_metrics(json.loads(row[0]), json.loads(row[1]))

    flow_metrics = TradeFlowMetrics(available=False, detail="Trade-Infrastruktur nicht vorhanden.")
    if "public_trade_events" in tables:
        rows = conn.execute(
            "SELECT price, size, side FROM public_trade_events WHERE provider = ? AND provider_market_id = ? "
            "ORDER BY traded_at DESC LIMIT 200",
            (provider, provider_market_id),
        ).fetchall()
        trades = [{"price": r[0], "size": r[1], "side": r[2]} for r in rows]
        flow_metrics = compute_trade_flow_metrics(trades)

    wallet_metrics = WalletConcentrationMetrics(available=False, detail="Wallet-Infrastruktur nicht vorhanden.")
    if "public_wallet_positions" in tables:
        latest = conn.execute(
            "SELECT MAX(captured_at) FROM public_wallet_positions WHERE provider = ? AND provider_market_id = ?",
            (provider, provider_market_id),
        ).fetchone()
        if latest and latest[0]:
            rows = conn.execute(
                "SELECT wallet_address, amount FROM public_wallet_positions "
                "WHERE provider = ? AND provider_market_id = ? AND captured_at = ?",
                (provider, provider_market_id, latest[0]),
            ).fetchall()
            holders = [{"wallet_address": r[0], "amount": r[1]} for r in rows]
            wallet_metrics = compute_wallet_concentration(holders)
        else:
            wallet_metrics = WalletConcentrationMetrics(available=False, detail="Keine Wallet-Positionsdaten erfasst (flow-fetch noch nicht ausgeführt).")

    return book_metrics, flow_metrics, wallet_metrics
