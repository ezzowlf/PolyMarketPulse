"""Real, mocked (no live network) tests for the NY Fed EFFR fallback
provider — the real fix for the Fed Funds policy rate's previously
unfillable fallback gap."""

from __future__ import annotations

from datetime import date

from polymarketpulse.providers import nyfed


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.content = b"x"

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def _payload(entries: list[dict]) -> dict:
    return {"refRates": entries}


def test_parses_real_shape_response_and_picks_the_effr_entry() -> None:
    payload = _payload([
        {"effectiveDate": "2026-08-12", "type": "SOFRAI", "average30day": 3.6},
        {"effectiveDate": "2026-08-12", "type": "EFFR", "percentRate": 3.63,
         "targetRateFrom": 3.50, "targetRateTo": 3.75},
        {"effectiveDate": "2026-08-12", "type": "OBFR", "percentRate": 3.63},
    ])
    from unittest.mock import patch

    with patch("polymarketpulse.providers.nyfed.httpx.get", return_value=_FakeResponse(payload)):
        result = nyfed.fetch_effr()
    assert result == [(date(2026, 8, 12), 3.63)]


def test_returns_none_when_no_effr_entry_present() -> None:
    from unittest.mock import patch

    payload = _payload([{"effectiveDate": "2026-08-12", "type": "SOFRAI", "average30day": 3.6}])
    with patch("polymarketpulse.providers.nyfed.httpx.get", return_value=_FakeResponse(payload)):
        result = nyfed.fetch_effr()
    assert result is None


def test_returns_none_on_malformed_response() -> None:
    from unittest.mock import patch

    with patch("polymarketpulse.providers.nyfed.httpx.get", return_value=_FakeResponse({"status": "error"})):
        result = nyfed.fetch_effr()
    assert result is None
