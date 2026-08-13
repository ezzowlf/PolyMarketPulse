from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from polymarketpulse.research_queue import MarketSignal, compute_priority
from polymarketpulse.storage import Storage


def test_early_signal_is_persisted_and_only_prioritizes_research(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "social.db")
    now = datetime.now(UTC).isoformat()
    storage.connection.execute("INSERT INTO markets(market_id,provider,provider_market_id,question,slug,url,first_seen_at,last_seen_at,resolution_status) VALUES('m','polymarket','m','Will port close?','m','https://market',?,?,'open')", (now, now))
    storage.connection.commit()
    signal = {"signal_id":"s1","source_type":"telegram_public","provider":"telegram","canonical_url":"https://t.me/example/1","detected_at":now,"summary":"Unverified shipping disruption report.","raw_reference":"https://t.me/example/1","origin_cluster":"telegram:example:1","signal_status":"EARLY_SIGNAL","verification_status":"UNVERIFIED","confidence":0.3}
    assert storage.save_social_signal(signal, market_ids=("m",))
    stored = storage.get_social_signals("m")
    assert stored[0]["verification_status"] == "UNVERIFIED"
    assert storage.connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    base = MarketSignal("m","q",None,None,0.5,0.5,100,0,0,True)
    warned = MarketSignal("m","q",None,None,0.5,0.5,100,0,0,True,early_signal_count=1)
    assert compute_priority(warned).priority_score > compute_priority(base).priority_score
    storage.close()
