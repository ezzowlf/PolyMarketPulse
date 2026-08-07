from polymarketpulse.prediction.reliability import (
    LEVEL_HIGH,
    LEVEL_INSUFFICIENT,
    LEVEL_LOW,
    compute_market_reliability,
)


def test_no_inputs_is_insufficient() -> None:
    r = compute_market_reliability(None, None, None, None, None, False)
    assert r.level == LEVEL_INSUFFICIENT
    assert r.score is None


def test_thin_orderbook_lowers_reliability() -> None:
    good = compute_market_reliability(80.0, 0.0, False, 10.0, None, False)
    thin = compute_market_reliability(80.0, 0.0, True, 10.0, None, False)
    assert thin.score < good.score


def test_high_wallet_concentration_lowers_reliability() -> None:
    low_conc = compute_market_reliability(80.0, 0.0, False, 5.0, None, False)
    high_conc = compute_market_reliability(80.0, 0.0, False, 90.0, None, False)
    assert high_conc.score < low_conc.score


def test_clean_high_clarity_market_scores_high() -> None:
    r = compute_market_reliability(90.0, 0.0, False, 5.0, 0.0, False)
    assert r.level == LEVEL_HIGH


def test_bad_signals_across_the_board_score_low() -> None:
    r = compute_market_reliability(10.0, 0.9, True, 90.0, 80.0, True)
    assert r.level == LEVEL_LOW
