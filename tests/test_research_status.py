from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from polymarketpulse.research_status import source_availability
from polymarketpulse.storage import Storage


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    result = Storage(tmp_path / "test.db")
    result.connection.execute(
        "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, first_seen_at, last_seen_at, resolution_status) VALUES ('m1', 'polymarket', 'm1', 'Test?', 'm1', 'https://x', ?, ?, 'open')",
        (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
    )
    result.connection.commit()
    yield result
    result.close()


def _run(status: str, *, accepted: int = 0) -> dict:
    return {
        "run_at": datetime.now(UTC).isoformat(), "provider": "polymarket", "provider_market_id": "m1",
        "question": "Test?", "sources_requested": 1, "sources_accepted": accepted,
        "detail": {"source_fetch_status": status, "source_attempts": [
            {"provider": "gdelt", "role": "discovery", "status": status, "reason": "test"},
        ], "alternative_providers": []},
    }


def test_fetch_failure_is_not_presented_as_no_evidence(storage: Storage) -> None:
    storage.save_research_run(_run("SOURCE_FETCH_FAILED"))
    result = source_availability(storage, "m1")
    assert result["status"] == "SOURCE_UNREACHABLE"
    assert result["severity"] == "info"
    assert result["retry_status"] == "BACKOFF"
    assert result["provider_attempts"][0]["provider"] == "gdelt"
    assert "keine fehlenden Informationen erfunden" in result["message"]


def test_successful_empty_fetch_is_relevant_evidence_absence(storage: Storage) -> None:
    storage.save_research_run(_run("OK"))
    result = source_availability(storage, "m1")
    assert result["status"] == "NO_RELEVANT_EVIDENCE"
    assert result["last_successful_fetch"]
