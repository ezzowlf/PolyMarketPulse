from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from polymarketpulse.ai import service as ai_service
from polymarketpulse.config import Settings
from polymarketpulse.models import Market
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage


class FakeClient:
    def generate_structured(self, system_prompt, user_prompt, schema_model, schema_name):
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return (
            {
                "summary": "Fake",
                "supporting_factors": [],
                "opposing_factors": [],
                "relevant_news": [],
                "data_gaps": [],
                "uncertainties": [],
                "market_move_explanation": "Fake",
                "confidence_in_analysis": 0.5,
                "source_ids": [],
                "disclaimer": "Research-Hinweis – keine Wettaufforderung.",
            },
            3,
            4,
        )


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    yield s
    s.close()


FAKE_KEY = "sk-THIS-MUST-NEVER-APPEAR-IN-THE-DATABASE"


def test_ai_run_row_never_stores_api_key_or_raw_prompt(storage: Storage) -> None:
    settings = replace(
        Settings.load(),
        database_path=Path("unused"),
        ai_enabled=True,
        openai_api_key=FAKE_KEY,
    )
    market = Market(
        provider="polymarket",
        provider_market_id="1",
        condition_id="",
        question="Will it happen?",
        slug="will-it-happen",
        liquidity=50000,
        volume_24h=20000,
        yes_price=0.6,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute("SELECT market_id FROM markets LIMIT 1").fetchone()[0]

    ai_service.explain_market(storage, settings, market_id, client=FakeClient())

    rows = storage.connection.execute("SELECT * FROM ai_analysis_runs").fetchall()
    assert len(rows) == 1
    row_text = str(rows[0])
    assert FAKE_KEY not in row_text
    assert "system_prompt" not in [d[0] for d in storage.connection.execute("PRAGMA table_info(ai_analysis_runs)")]


def test_ai_analysis_runs_table_has_no_prompt_or_key_columns(storage: Storage) -> None:
    columns = {row[1] for row in storage.connection.execute("PRAGMA table_info(ai_analysis_runs)")}
    forbidden = {"api_key", "openai_api_key", "prompt", "system_prompt", "user_prompt", "raw_prompt"}
    assert columns.isdisjoint(forbidden)
