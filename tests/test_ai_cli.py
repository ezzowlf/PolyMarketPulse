import json

import pytest

from polymarketpulse import cli


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKETPULSE_DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")


def test_ai_status_reports_disabled(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ai-status", "--json"])
    exit_code = args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["enabled"] is False
    assert data["ready"] is False


def test_ai_explain_market_exits_nonzero_when_disabled(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ai-explain-market", "123"])
    exit_code = args.func(args)
    err = capsys.readouterr().err
    assert exit_code == 3
    assert "AI nicht verfügbar" in err
    assert "OPENAI_API_KEY" not in err or "sk-" not in err  # never echoes a key


def test_ai_ask_exits_nonzero_when_disabled(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ai-ask", "Why did this move?"])
    exit_code = args.func(args)
    assert exit_code == 3


def test_ai_smoke_test_refuses_without_enabled_flag(capsys) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ai-smoke-test", "--market-id", "123"])
    exit_code = args.func(args)
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "abgebrochen" in err


def test_ai_smoke_test_refuses_with_flag_but_no_key(capsys, monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKETPULSE_AI_ENABLED", "true")
    parser = cli.build_parser()
    args = parser.parse_args(["ai-smoke-test", "--market-id", "123"])
    exit_code = args.func(args)
    assert exit_code == 1
