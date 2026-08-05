"""Generates the 4 required Phase-7 acceptance examples (YES, NO, NO_BET,
INSUFFICIENT_DATA) using the real, unmodified prediction/explanation engine
(`polymarketpulse.prediction.compute_prediction` +
`polymarketpulse.ai.service.explain_recommendation`).

No GPT call is made here — AI stays disabled, so every explanation comes
from the deterministic rule-based fallback (`ai/fallback.py`). That layer is
still the real, production code path (used automatically whenever AI is
unavailable), not a mock. The one real GPT-5 nano call is done separately as
the manual live-smoke-test.

Example D (INSUFFICIENT_DATA) is generated directly against the real,
current production database at POLYMARKETPULSE_DATABASE_PATH: as of writing
it holds 34 markets and only 10 resolved outcomes, and the `category` field
is populated with each market's own (effectively unique) question text
rather than a normalized topic taxonomy — so the historical base-rate query
in `compute_prediction` essentially never finds >=5 comparable resolved
markets for any single real market today. That is documented here, not
worked around.

Examples A-C (YES / NO / NO_BET) require a category with >=5 comparable
resolved markets, which the production data does not yet have. They are
generated against a temporary, isolated SQLite database seeded with
category-consistent, realistic (non-fantasy) resolved-market history using
the exact same schema and migrations as production — then run through the
unmodified engine. This is the same pattern already used by the automated
test suite (tests/test_ai_explain_recommendation.py, tests/test_backtest.py).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from polymarketpulse.ai import service as ai_service
from polymarketpulse.config import Settings
from polymarketpulse.models import Market
from polymarketpulse.signals import generate_signals
from polymarketpulse.storage import Storage

OUT_PATH = Path(__file__).resolve().parent.parent / "analysis" / "reports" / "phase7_acceptance_examples.json"


def _seed_resolved_history(conn: sqlite3.Connection, n_yes: int, n_no: int, category: str) -> None:
    for i in range(n_yes):
        pmid = f"{category}-yes-{i}"
        conn.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
            "first_seen_at, last_seen_at, resolution_status, category) "
            "VALUES (?, 'polymarket', ?, ?, 'x', 'https://x', ?, ?, 'resolved', ?)",
            (pmid, pmid, f"Historischer Fall {i} (YES) in {category}",
             datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), category),
        )
        conn.execute(
            "INSERT INTO market_resolutions (provider, provider_market_id, resolved_at, winning_outcome, status, detected_at) "
            "VALUES ('polymarket', ?, ?, 'Yes', 'resolved', ?)",
            (pmid, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
    for i in range(n_no):
        pmid = f"{category}-no-{i}"
        conn.execute(
            "INSERT INTO markets (market_id, provider, provider_market_id, question, slug, url, "
            "first_seen_at, last_seen_at, resolution_status, category) "
            "VALUES (?, 'polymarket', ?, ?, 'x', 'https://x', ?, ?, 'resolved', ?)",
            (pmid, pmid, f"Historischer Fall {i} (NO) in {category}",
             datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), category),
        )
        conn.execute(
            "INSERT INTO market_resolutions (provider, provider_market_id, resolved_at, winning_outcome, status, detected_at) "
            "VALUES ('polymarket', ?, ?, 'No', 'resolved', ?)",
            (pmid, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
    conn.commit()


def _seed_target_market(storage: Storage, category: str, yes_price: float, liquidity: float = 150000) -> str:
    market = Market(
        provider="polymarket", provider_market_id="target", condition_id="", question=f"Zielmarkt in {category}",
        slug="target", category=category, liquidity=liquidity, volume_24h=40000, yes_price=yes_price,
        start_at=datetime.now(UTC) - timedelta(hours=2),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    return storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = 'target'"
    ).fetchone()[0]


def _build_isolated_example(tmp_db_path: Path, category: str, n_yes: int, n_no: int, yes_price: float) -> dict:
    storage = Storage(tmp_db_path)
    _seed_resolved_history(storage.connection, n_yes=n_yes, n_no=n_no, category=category)
    market_id = _seed_target_market(storage, category=category, yes_price=yes_price)

    settings = replace(Settings.load(), database_path=tmp_db_path, ai_enabled=False, openai_api_key=None)
    response = ai_service.explain_recommendation(storage, settings, market_id)
    storage.close()
    return {
        "market_id": market_id,
        "category": category,
        "seed": {"n_yes": n_yes, "n_no": n_no, "market_yes_price": yes_price},
        "data_source": "isolierte Test-Datenbank (echte Engine, realistische Trainingsdaten, gleiche Migrationen wie Produktion)",
        "response": response.model_dump(),
    }


def _build_production_insufficient_data_example() -> dict:
    settings = Settings.load()
    settings = replace(settings, ai_enabled=False, openai_api_key=None)
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    market_id = storage.connection.execute(
        "SELECT market_id FROM markets WHERE resolution_status != 'resolved' ORDER BY last_seen_at DESC LIMIT 1"
    ).fetchone()[0]
    n_resolved_total = storage.connection.execute("SELECT COUNT(*) FROM market_resolutions WHERE status='resolved'").fetchone()[0]
    response = ai_service.explain_recommendation(storage, settings, market_id)
    storage.close()
    return {
        "market_id": market_id,
        "data_source": "echte Produktions-Datenbank (POLYMARKETPULSE_DATABASE_PATH)",
        "missing_data_explanation": (
            f"Die Produktionsdatenbank enthält aktuell {n_resolved_total} aufgelöste Märkte insgesamt. Das "
            "`category`-Feld wird je Markt mit dem (praktisch eindeutigen) Fragetext befüllt statt mit einer "
            "normalisierten Themen-Taxonomie — dadurch findet die Basisraten-Abfrage in compute_prediction() "
            "für so gut wie keinen einzelnen Markt >= 5 vergleichbare aufgelöste Fälle in derselben Kategorie. "
            "Das ist der dokumentierte, reale Grund für INSUFFICIENT_DATA in Produktion, keine künstliche "
            "Einschränkung des Beispiels."
        ),
        "response": response.model_dump(),
    }


def main() -> None:
    import tempfile

    examples: dict[str, dict] = {}

    with tempfile.TemporaryDirectory() as tmp:
        # Example A — YES: strong historical base rate + market underpricing.
        db_a = Path(tmp) / "example_a.db"
        examples["A_YES"] = _build_isolated_example(db_a, category="beispiel-yes-esport", n_yes=18, n_no=2, yes_price=0.55)

        # Example B — NO: historical base rate strongly against, market overpricing YES.
        db_b = Path(tmp) / "example_b.db"
        examples["B_NO"] = _build_isolated_example(db_b, category="beispiel-no-esport", n_yes=3, n_no=17, yes_price=0.60)

        # Example C — NO_BET: market price already matches the historical base rate.
        db_c = Path(tmp) / "example_c.db"
        examples["C_NO_BET"] = _build_isolated_example(db_c, category="beispiel-no-bet-esport", n_yes=10, n_no=10, yes_price=0.50)

    # Example D — INSUFFICIENT_DATA: real production data, real limitation.
    examples["D_INSUFFICIENT_DATA"] = _build_production_insufficient_data_example()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(examples, indent=2, ensure_ascii=False), encoding="utf-8")

    for key, ex in examples.items():
        r = ex["response"]
        pred = r["prediction"]
        exp = r["explanation"]
        print(f"--- {key} ---")
        print(f"  market_id={ex['market_id']}")
        print(f"  Markt YES={pred['market_yes_probability']}  Eigene YES={pred['estimated_yes_probability']}  "
              f"Netto-Edge={pred['net_yes_edge']}  Richtung={exp['direction']}  Empfehlung={pred['recommendation']}")
        print(f"  vergleichbare Fälle={pred['comparable_sample_size']}  Vertrauen={pred['confidence_score']}  "
              f"Datenqualität={pred['data_quality_score']}")
    print(f"\nVollständiger Bericht: {OUT_PATH}")


if __name__ == "__main__":
    main()
