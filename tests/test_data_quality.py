from datetime import UTC, datetime, timedelta

from polymarketpulse.data_quality import assess_market, assess_snapshot_sequence
from polymarketpulse.models import Market


def _market(**overrides) -> Market:
    now = datetime.now(UTC)
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "0xabc",
        "question": "Test",
        "slug": "test",
        "liquidity": 50000,
        "volume_24h": 20000,
        "yes_price": 0.6,
        "no_price": 0.4,
        "spread": 0.02,
        "category": "Politics",
        "end_at": now + timedelta(days=5),
        "start_at": now - timedelta(days=1),
    }
    defaults.update(overrides)
    return Market(**defaults)


def test_complete_market_scores_high() -> None:
    report = assess_market(_market())
    assert report.score >= 90
    assert not report.issues


def test_missing_required_fields_penalized() -> None:
    report = assess_market(_market(category=None, spread=None))
    assert report.score < 100
    assert any("Pflichtfelder" in issue for issue in report.issues)


def test_invalid_price_range_penalized() -> None:
    report = assess_market(_market(yes_price=1.5))
    assert any("ungültiger YES-Preis" in issue for issue in report.issues)


def test_negative_volume_penalized() -> None:
    report = assess_market(_market(volume_24h=-100))
    assert any("negatives Volumen" in issue for issue in report.issues)


def test_negative_liquidity_penalized() -> None:
    report = assess_market(_market(liquidity=-50))
    assert any("negative Liquidität" in issue for issue in report.issues)


def test_yes_no_inconsistency_penalized() -> None:
    report = assess_market(_market(yes_price=0.9, no_price=0.9))
    assert any("weicht von 1.0 ab" in issue for issue in report.issues)


def test_end_before_start_penalized() -> None:
    now = datetime.now(UTC)
    report = assess_market(_market(start_at=now, end_at=now - timedelta(days=1)))
    assert any("Enddatum liegt vor dem Startdatum" in issue for issue in report.issues)


def test_score_never_negative() -> None:
    report = assess_market(
        _market(yes_price=5.0, no_price=-5.0, liquidity=-1, volume_24h=-1, spread=5.0, category=None)
    )
    assert report.score >= 0.0


def test_duplicate_snapshot_detection() -> None:
    rows = [("2026-01-01T00:00:00", 0.5), ("2026-01-01T00:00:00", 0.5), ("2026-01-02T00:00:00", 0.6)]
    result = assess_snapshot_sequence(rows)
    assert result.duplicate_snapshots == 1


def test_out_of_order_detection() -> None:
    rows = [("2026-01-02T00:00:00", 0.5), ("2026-01-01T00:00:00", 0.6)]
    result = assess_snapshot_sequence(rows)
    assert result.out_of_order_snapshots == 1


def test_clean_sequence_has_no_issues() -> None:
    rows = [("2026-01-01T00:00:00", 0.5), ("2026-01-02T00:00:00", 0.6)]
    result = assess_snapshot_sequence(rows)
    assert result.duplicate_snapshots == 0
    assert result.out_of_order_snapshots == 0
