from polymarketpulse.prediction.manipulation import compute_manipulation_risk


def test_no_signals_is_low_risk() -> None:
    r = compute_manipulation_risk(orderbook_thin=False, large_trade_ratio=0.1, price_moved_without_evidence=False, wallet_concentration_score=5.0, deadline_hours=200)
    assert r.risk_score < 20


def test_wallet_concentration_alone_increases_risk() -> None:
    low = compute_manipulation_risk(None, None, False, 5.0, None)
    high = compute_manipulation_risk(None, None, False, 90.0, None)
    assert high.risk_score > low.risk_score


def test_price_move_without_evidence_increases_risk() -> None:
    without = compute_manipulation_risk(False, 0.1, False, 5.0, None)
    with_move = compute_manipulation_risk(False, 0.1, True, 5.0, None)
    assert with_move.risk_score > without.risk_score


def test_reasons_are_neutral_never_accusatory() -> None:
    r = compute_manipulation_risk(True, 0.9, True, 90.0, 5.0)
    combined = " ".join(r.reasons).lower() + r.detail.lower()
    for forbidden in ("insider", "fraud", "betrug", "täter", "schuldig"):
        assert forbidden not in combined


def test_confidence_reflects_data_points_available() -> None:
    few = compute_manipulation_risk(None, None, False, None, None)
    many = compute_manipulation_risk(True, 0.5, True, 50.0, 10.0)
    assert many.confidence > few.confidence
