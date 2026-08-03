from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.ai.context_builder import (
    MAX_DESCRIPTION_CHARS,
    MAX_NEWS_ITEMS,
    MAX_PRICE_HISTORY_POINTS,
    build_market_context,
    context_hash,
)
from polymarketpulse.models import Market
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


def _market(**overrides) -> Market:
    defaults = {
        "provider": "polymarket",
        "provider_market_id": "1",
        "condition_id": "",
        "question": "Will the Fed cut rates?",
        "slug": "fed-cut",
        "description": "x" * 2000,
        "liquidity": 50000,
        "volume_24h": 20000,
        "yes_price": 0.6,
        "spread": 0.02,
        "start_at": datetime.now(UTC) - timedelta(hours=1),
        "end_at": datetime.now(UTC) + timedelta(days=5),
    }
    defaults.update(overrides)
    return Market(**defaults)


def _seed(storage: Storage, market: Market) -> str:
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]


def test_context_none_for_unknown_market(storage: Storage) -> None:
    assert build_market_context(storage, "does-not-exist") is None


def test_context_includes_core_fields(storage: Storage) -> None:
    market_id = _seed(storage, _market())
    context = build_market_context(storage, market_id)
    assert context is not None
    assert context.market_id == market_id
    assert context.provider == "polymarket"
    assert context.yes_price == 0.6


def test_description_is_truncated(storage: Storage) -> None:
    market_id = _seed(storage, _market())
    context = build_market_context(storage, market_id)
    assert len(context.description) <= MAX_DESCRIPTION_CHARS


def test_price_history_is_bounded(storage: Storage) -> None:
    market = _market()
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]

    for i in range(MAX_PRICE_HISTORY_POINTS + 15):
        changed = _market(yes_price=0.5 + (i % 5) * 0.01)
        run_id = storage.start_run("polymarket")
        storage.save(run_id, [(changed, generate_signals(changed))])

    context = build_market_context(storage, market_id)
    assert len(context.price_history) <= MAX_PRICE_HISTORY_POINTS


def test_news_items_are_bounded(storage: Storage) -> None:
    market_id = _seed(storage, _market())
    provider_market_id = storage.connection.execute(
        "SELECT provider_market_id FROM markets WHERE market_id = ?", (market_id,)
    ).fetchone()[0]

    now = datetime.now(UTC).isoformat()
    for i in range(MAX_NEWS_ITEMS + 10):
        storage.connection.execute(
            "INSERT INTO news_events (source, source_url, title, published_at, fetched_at, content_hash) "
            "VALUES ('test', ?, ?, ?, ?, ?)",
            (f"https://x/{i}", f"News {i}", now, now, f"hash{i}"),
        )
        news_id = storage.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        storage.connection.execute(
            "INSERT INTO news_market_links (news_event_id, provider, provider_market_id, match_reason, "
            "matched_terms, confidence, created_at) VALUES (?, 'polymarket', ?, 'test', 'fed', 0.5, ?)",
            (news_id, provider_market_id, now),
        )
    storage.connection.commit()

    context = build_market_context(storage, market_id)
    assert len(context.relevant_news) <= MAX_NEWS_ITEMS


def test_context_hash_is_stable_for_same_data(storage: Storage) -> None:
    market_id = _seed(storage, _market())
    a = build_market_context(storage, market_id)
    b = build_market_context(storage, market_id)
    assert context_hash(a) == context_hash(b)


def test_context_hash_changes_with_price(storage: Storage) -> None:
    market_id = _seed(storage, _market(yes_price=0.6))
    a = build_market_context(storage, market_id)

    changed = _market(yes_price=0.9)
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(changed, generate_signals(changed))])
    b = build_market_context(storage, market_id)

    assert context_hash(a) != context_hash(b)


def test_context_never_exceeds_reasonable_size(storage: Storage) -> None:
    market_id = _seed(storage, _market())
    context = build_market_context(storage, market_id)
    serialized = context.model_dump_json()
    # Generous ceiling: bounded lists + truncated text should stay well
    # under an arbitrary "not a full table dump" size.
    assert len(serialized) < 20000
