"""One-off script (Part D of the P0 hardening round-2 task) — re-run the
same 30-market acceptance set documented in HANDOFF.md's last run through
the post-fix pipeline and report the forecast_maturity distribution before
vs after. Not a permanent test; ad hoc verification script."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarketpulse.ai import service as ai_service
from polymarketpulse.storage import Storage

MARKET_IDS = [
    "2774056", "polymarket:3231771", "561996", "polymarket:1130017", "polymarket:1163699",
    "polymarket:2910437", "polymarket:3128889", "polymarket:3253865", "665374", "polymarket:1469755",
    "polymarket:2063130", "polymarket:2063134", "polymarket:2507618", "polymarket:2937525",
    "polymarket:2252242", "polymarket:2252243", "polymarket:2252244", "polymarket:2252245",
    "polymarket:2252246", "polymarket:3239538", "polymarket:3241070", "polymarket:3241073",
    "polymarket:3241078", "polymarket:3241080", "polymarket:3362450", "3128024", "3254068",
    "3275594", "3286340", "polymarket:2771492",
]

CENTRAL_BANKS_IDS = {
    "polymarket:2252242", "polymarket:2252243", "polymarket:2252244",
    "polymarket:2252245", "polymarket:2252246",
}


def main() -> None:
    db_path = Path(__file__).resolve().parent.parent / "data" / "polymarketpulse.db"
    storage = Storage(db_path, auto_migrate=False)

    dist: dict[str, int] = {}
    rows = []
    errors = []
    for mid in MARKET_IDS:
        try:
            result = ai_service.get_prediction(storage, mid)
        except Exception as exc:  # noqa: BLE001
            errors.append((mid, repr(exc)))
            continue
        maturity = result.forecast_maturity
        dist[maturity] = dist.get(maturity, 0) + 1
        rows.append((mid, maturity, result.forecast_status, result.independent_probability))

    print("=== forecast_maturity distribution (AFTER, post-fix) ===")
    for k in ("NO_FORECAST", "PARTIAL_FORECAST", "SUPPORTED_FORECAST", "MATURE_FORECAST"):
        print(f"  {k}: {dist.get(k, 0)}")
    print(f"  total queried: {len(rows)} (of {len(MARKET_IDS)} requested), errors: {len(errors)}")
    if errors:
        for mid, err in errors:
            print(f"  ERROR {mid}: {err}")

    print("\n=== CENTRAL_BANKS markets (must remain SUPPORTED_FORECAST, unchanged) ===")
    for mid, maturity, status, ip in rows:
        if mid in CENTRAL_BANKS_IDS:
            print(f"  {mid}: maturity={maturity} status={status} independent_probability={ip}")

    print("\n=== full per-market table ===")
    for mid, maturity, status, ip in rows:
        print(f"  {mid:30s} maturity={maturity:20s} status={status:25s} independent_probability={ip}")


if __name__ == "__main__":
    main()
