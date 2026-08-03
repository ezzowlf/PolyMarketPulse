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
    assert data["schema_version"] == 5
    assert data["markets"] == 0


def test_db_migrate_reports_schema_version(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["db-migrate", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["schema_version"] == 5


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
