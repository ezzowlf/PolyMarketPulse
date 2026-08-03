from polymarketpulse.price_analytics import PricePoint, compute_price_analytics


def _points() -> list[PricePoint]:
    return [
        PricePoint("2026-01-01T00:00:00", 0.40, liquidity=1000, volume_24h=100, spread=0.05),
        PricePoint("2026-01-01T01:00:00", 0.45, liquidity=1100, volume_24h=120, spread=0.04),
        PricePoint("2026-01-01T02:00:00", 0.42, liquidity=1200, volume_24h=110, spread=0.03),
        PricePoint("2026-01-01T03:00:00", 0.50, liquidity=1300, volume_24h=150, spread=0.02),
    ]


def test_price_change_and_pct() -> None:
    result = compute_price_analytics(_points())
    assert result.price_change is not None
    assert round(result.price_change, 2) == 0.10
    assert result.price_change_pct is not None


def test_moving_averages_computed() -> None:
    result = compute_price_analytics(_points())
    assert result.moving_average_short is not None
    assert result.moving_average_long is not None  # falls back to available window


def test_volatility_is_nonnegative() -> None:
    result = compute_price_analytics(_points())
    assert result.volatility is not None
    assert result.volatility >= 0


def test_trend_reversal_detected() -> None:
    # up, down, up -> one reversal
    result = compute_price_analytics(_points())
    assert result.trend_reversals >= 1


def test_liquidity_and_spread_trend_labels() -> None:
    result = compute_price_analytics(_points())
    assert result.liquidity_trend == "steigend"
    assert result.spread_trend == "fallend"


def test_empty_points_do_not_crash() -> None:
    result = compute_price_analytics([])
    assert result.sample_count == 0
    assert result.price_change is None
    assert result.volatility is None


def test_single_point_does_not_crash() -> None:
    result = compute_price_analytics([PricePoint("2026-01-01T00:00:00", 0.5)])
    assert result.price_change is None
    assert result.moving_average_short == 0.5
