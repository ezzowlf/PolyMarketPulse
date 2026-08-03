from datetime import UTC, datetime

from polymarketpulse.news.reactions import compute_reaction


def test_reaction_detected_above_threshold() -> None:
    published = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    history = [
        ("2026-01-01T10:00:00+00:00", 0.50),
        ("2026-01-01T13:00:00+00:00", 0.60),
    ]
    result = compute_reaction(1, published, history)
    assert result.price_before == 0.50
    assert result.price_after == 0.60
    assert result.reacted is True


def test_no_reaction_below_threshold() -> None:
    published = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    history = [
        ("2026-01-01T10:00:00+00:00", 0.50),
        ("2026-01-01T13:00:00+00:00", 0.505),
    ]
    result = compute_reaction(1, published, history)
    assert result.reacted is False


def test_no_after_price_within_window_returns_none_change() -> None:
    published = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    history = [("2026-01-01T10:00:00+00:00", 0.50)]
    result = compute_reaction(1, published, history, window_hours=1.0)
    assert result.price_after is None
    assert result.price_change is None
    assert result.reacted is False


def test_malformed_timestamps_are_skipped_without_crashing() -> None:
    published = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    history = [("not-a-date", 0.5), ("2026-01-01T13:00:00+00:00", 0.6)]
    result = compute_reaction(1, published, history)
    assert result.price_after == 0.6
