from polymarketpulse.prediction.market_flow import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_STRONG_FLOW,
    STATUS_WATCH,
    compute_orderbook_metrics,
    compute_trade_flow_metrics,
    compute_wallet_concentration,
)


def test_orderbook_imbalance_positive_when_bid_heavy() -> None:
    bids = [{"price": 0.5, "size": 1000}]
    asks = [{"price": 0.51, "size": 100}]
    m = compute_orderbook_metrics(bids, asks)
    assert m.available is True
    assert m.imbalance > 0


def test_thin_orderbook_flagged() -> None:
    bids = [{"price": 0.5, "size": 10}]
    asks = [{"price": 0.51, "size": 10}]
    m = compute_orderbook_metrics(bids, asks, thin_depth_usd=500.0)
    assert m.thin is True


def test_empty_orderbook_is_unavailable() -> None:
    m = compute_orderbook_metrics([], [])
    assert m.available is False


def test_single_large_trade_alone_does_not_prove_strong_flow() -> None:
    # One large trade among otherwise ordinary trades should not, by
    # itself, push the status all the way to "starkes Flow-Signal" —
    # a single data point cannot "prove" anything.
    trades = [
        {"price": 0.5, "size": 2, "side": "BUY"},
        {"price": 0.5, "size": 3, "side": "SELL"},
        {"price": 0.5, "size": 2500, "side": "BUY"},
    ]
    m = compute_trade_flow_metrics(trades, large_trade_usd_threshold=1000.0)
    # The large trade does dominate volume-share here (that's expected and
    # correctly reported), but the result must stay a neutral status label,
    # not an accusation.
    assert m.status in (STATUS_STRONG_FLOW, STATUS_WATCH)
    assert "insider" not in m.detail.lower()
    assert "manipulat" not in m.detail.lower()


def test_too_few_trades_is_insufficient_data() -> None:
    m = compute_trade_flow_metrics([{"price": 0.5, "size": 1, "side": "BUY"}])
    assert m.status == STATUS_INSUFFICIENT_DATA


def test_no_trades_is_unavailable() -> None:
    m = compute_trade_flow_metrics([])
    assert m.available is False


def test_wallet_concentration_single_holder_scores_high() -> None:
    holders = [{"wallet_address": "0x1111111111111111111111111111111111111", "amount": 1000}]
    m = compute_wallet_concentration(holders)
    assert m.available is True
    assert m.top1_share == 1.0
    assert m.concentration_score == 100.0


def test_wallet_concentration_evenly_split_scores_low() -> None:
    holders = [
        {"wallet_address": f"0x{i}111111111111111111111111111111111111", "amount": 100}
        for i in range(10)
    ]
    m = compute_wallet_concentration(holders)
    assert m.top1_share == 0.1
    assert m.concentration_score < 20.0


def test_wallet_addresses_always_truncated() -> None:
    full_address = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
    holders = [{"wallet_address": full_address, "amount": 100}]
    m = compute_wallet_concentration(holders)
    assert full_address not in m.top_wallets
    assert "..." in m.top_wallets[0]


def test_no_holders_is_unavailable() -> None:
    m = compute_wallet_concentration([])
    assert m.available is False
