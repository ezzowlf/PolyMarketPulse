from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from polymarketpulse.coherence import audit_relationship
from polymarketpulse.lineage import audit_market_lineage
from polymarketpulse.storage import Storage


def test_explicit_provenance_relationship_is_audited_not_title_overlap(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "coherence.db")
    now = datetime.now(UTC).isoformat()
    for market_id in ("parent", "child"):
        storage.connection.execute("INSERT INTO markets(market_id,provider,provider_market_id,question,slug,url,first_seen_at,last_seen_at,resolution_status) VALUES(?,?,?,?,'s','https://x',?,?,'open')", (market_id, "polymarket", market_id, market_id, now, now))
    storage.connection.commit()
    rel = storage.save_market_relationship("parent", "child", "PARENT_CHILD", "https://official.example/rule", "RESOLUTION_RULE", .95, "Child outcome requires parent outcome.")
    assert rel
    result = storage.coherence_audit(rel, .4, .6)
    assert result and result["status"] == "COHERENCE_WARNING"
    assert audit_relationship("PARENT_CHILD", .7, .4).status == "CONSISTENT"
    storage.close()


def test_lineage_marks_published_forecast_without_claim_as_broken(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "lineage.db")
    now = datetime.now(UTC).isoformat()
    storage.connection.execute("INSERT INTO markets(market_id,provider,provider_market_id,question,slug,url,first_seen_at,last_seen_at,resolution_status) VALUES('m','polymarket','m','q','s','https://x',?,?,'open')", (now, now))
    storage.save_prediction_snapshot("m", "polymarket", "m", None, "v", .5, .6, .1, 50, "WATCH", 1, published_forecast_probability=.6)
    storage.connection.commit()
    assert audit_market_lineage(storage.connection, "m")["status"] == "BROKEN"
    report = audit_market_lineage(storage.connection, "m")
    storage.save_lineage_audit("m", report)
    storage.save_lineage_audit("m", report)
    assert storage.connection.execute("SELECT COUNT(*) FROM lineage_audits").fetchone()[0] == 1
    storage.close()
