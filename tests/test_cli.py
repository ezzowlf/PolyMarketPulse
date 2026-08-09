import json
from pathlib import Path

import pytest

from polymarketpulse import cli


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKETPULSE_DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("POLYMARKETPULSE_TELEGRAM_ENABLED", "false")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # Never let the local .env's real setting (news is enabled in
    # production now) leak into tests — this must stay deterministic and
    # network-free regardless of ambient environment/.env state. Tests that
    # specifically exercise the enabled path mock the network layer and
    # override this explicitly.
    monkeypatch.setenv("POLYMARKETPULSE_NEWS_ENABLED", "false")
    # Never let automated tests make a real OpenAI call — a real key may be
    # present in the local .env from a previous manual live-smoke-test.
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")


def test_providers_command_lists_registered_providers(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["providers", "--json"])
    exit_code = args.func(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    names = {row["name"] for row in data}
    assert exit_code == 0
    assert {"polymarket", "manifold", "kalshi", "metaculus", "predictit"} <= names


def test_provider_info_command(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["provider-info", "polymarket", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["name"] == "polymarket"
    assert data["real_money"] is True


def test_db_status_on_fresh_db(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["db-status", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["schema_version"] == 18
    assert data["markets"] == 0


def test_db_migrate_reports_schema_version(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["db-migrate", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["schema_version"] == 18


def test_signal_stats_on_empty_db_does_not_crash(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["signal-stats", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["signal_count"] == 0


def test_news_fetch_disabled_by_default_is_a_noop(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["news-fetch"])
    exit_code = args.func(args)
    err = capsys.readouterr().err
    assert exit_code == 0
    assert "deaktiviert" in err


def test_send_alerts_without_telegram_enabled_never_calls_send_message(monkeypatch) -> None:
    called = {"count": 0}
    monkeypatch.setattr(cli, "send_message", lambda *a, **k: called.__setitem__("count", called["count"] + 1))

    from polymarketpulse.config import Settings
    from polymarketpulse.storage import Storage

    settings = Settings.load()
    storage = Storage(settings.database_path)
    try:
        exit_code = cli._send_alerts(settings, storage, [])
    finally:
        storage.close()
    assert exit_code == 0
    assert called["count"] == 0


def _seed_market_for_cli() -> str:
    from datetime import UTC, datetime, timedelta

    from polymarketpulse.config import Settings
    from polymarketpulse.models import Market
    from polymarketpulse.signals import generate_signals
    from polymarketpulse.storage import Storage

    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    market = Market(
        provider="polymarket",
        provider_market_id="1",
        condition_id="",
        question="Will Team A win?",
        slug="team-a",
        category="esports",
        liquidity=100000,
        volume_24h=20000,
        yes_price=0.5,
        start_at=datetime.now(UTC) - timedelta(hours=1),
    )
    run_id = storage.start_run("polymarket")
    storage.save(run_id, [(market, generate_signals(market))])
    market_id = storage.connection.execute(
        "SELECT market_id FROM markets WHERE provider = 'polymarket' AND provider_market_id = '1'"
    ).fetchone()[0]
    storage.close()
    return market_id


def test_predict_command_outputs_binding_prediction(capsys) -> None:
    market_id = _seed_market_for_cli()
    parser = cli.build_parser()
    args = parser.parse_args(["predict", market_id, "--json"])
    exit_code = args.func(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert exit_code == 0
    assert data["market_id"] == market_id
    assert data["recommendation"] in (
        "STRONG_YES", "YES", "WATCH_YES", "NO_BET", "WATCH_NO", "NO", "STRONG_NO", "INSUFFICIENT_DATA",
    )


def test_predict_command_unknown_market_returns_error_code(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["predict", "does-not-exist", "--json"])
    exit_code = args.func(args)
    assert exit_code == 4


def test_explain_recommendation_command_uses_rule_based_fallback_without_key(capsys) -> None:
    market_id = _seed_market_for_cli()
    parser = cli.build_parser()
    args = parser.parse_args(["explain-recommendation", market_id, "--json"])
    exit_code = args.func(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert exit_code == 0
    assert data["meta"]["used_fallback"] is True
    assert data["explanation"]["recommendation"] == data["prediction"]["recommendation"]


def test_explain_recommendation_no_cache_flag_forces_recompute(capsys) -> None:
    market_id = _seed_market_for_cli()
    parser = cli.build_parser()
    args1 = parser.parse_args(["explain-recommendation", market_id, "--json"])
    args1.func(args1)
    capsys.readouterr()
    args2 = parser.parse_args(["explain-recommendation", market_id, "--no-cache", "--json"])
    exit_code = args2.func(args2)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["meta"]["cached"] is False


def test_cost_report_command_on_empty_db(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["cost-report", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["spent_today_usd"] == 0
    assert data["by_model"] == []


def test_backtest_command_on_empty_db_reports_zero_cases(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["backtest", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["n_evaluated"] == 0
    assert data["brier_score"] is None


def test_predict_command_text_output_includes_v2_fields(capsys) -> None:
    market_id = _seed_market_for_cli()
    parser = cli.build_parser()
    args = parser.parse_args(["predict", market_id])
    exit_code = args.func(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Deadline-Phase:" in out


def test_explain_recommendation_text_output_includes_v2_fields(capsys) -> None:
    market_id = _seed_market_for_cli()
    parser = cli.build_parser()
    args = parser.parse_args(["explain-recommendation", market_id])
    exit_code = args.func(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Deadline-Phase:" in out
