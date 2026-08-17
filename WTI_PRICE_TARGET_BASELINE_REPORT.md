# WTI $85 Price-Target Engine — Blocks A-D (Provider Audit + Active-Month Logic)

Market: `polymarket:3310013` — "Will WTI Crude Oil (WTI) hit (HIGH) $85 in August?"

## 1. Provider Audit (Block A)

**Pyth Hermes** — verified live during this round (real, unauthenticated HTTP calls, not assumed):

- Base URL: `https://hermes.pyth.network`
- `/v2/price_feeds?query=WTI` (discovery/search) — live, returns real named-contract-month feeds:
  `WTIQ6` (Aug 2026 delivery), `WTIU6` (Sep 2026), `WTIV6` (Oct 2026), `WTIX6` (Nov 2026), plus deprecated older-month feeds.
- `/v2/updates/price/latest?ids[]=<feed_id>&parsed=true` — live, unauthenticated, returned real current prices during this round:
  - WTIU6: **$84.38159** (publish_time 2026-08-17T19:20:41Z)
  - WTIV6: **$83.63606**
  - WTIX6: **$82.25837**
  (A normal contango curve — front month highest, consistent with real market behavior, not a stub/mock response.)
- Historical/OHLC candles: **not available** via the public Hermes REST API. Docs confirm no historical-candle endpoint exists; a live test against `/v2/updates/price/{timestamp}` for a real past timestamp returned `404 Update data not found`. The market's own resolution text points users to a *separate* service (`pythdata.app`) for historical 1-minute candles — not verified this round (out of scope for this block).
- **Important deadline**: Pyth's own docs state authentication (`PYTH_API_KEY`) becomes *required* on **2026-08-26** — 9 days from the "today" observed during this session. Any production integration must account for this soon.

**CME** — not audited this round (Pyth alone was sufficient to unblock Blocks C/D; CME fallback audit deferred, honestly not attempted, not claimed working).

## 2. Provider decision (Block B)

- `PRIMARY_MODEL_PROVIDER` = Pyth Hermes (`/v2/updates/price/latest`) — real, live, free, keyless (for now).
- `RESOLUTION_PROVIDER` = Pyth (per the market's own resolution rule).
- `RESOLUTION_FALLBACK` = CME — not yet integrated (not audited this round).

## 3. WTI Market Data Provider (Block C)

New module: `src/polymarketpulse/providers/pyth.py`.

- `fetch_latest_price(feed_id)` → `PythPrice(feed_id, price, confidence, publish_time, retrieved_at)`, or `None` on any failure (network error, malformed response, non-positive price) — never fabricates.
- `WTI_CONTRACT_FEED_IDS`: hand-curated, live-verified feed ids for the 2026 WTI contract months actually relevant to current markets (`WTIU6`/`WTIV6`/`WTIX6`).
- SSRF-guarded (`assert_safe_url`), response-size-capped (`MAX_RESPONSE_BYTES`), same pattern as the existing `providers/coingecko.py`.

## 4. Active Month Logic (Block D)

Implements the *exact* roll rule quoted in the real resolution text (3, or 4 if the 25th isn't a business day, business days before the 25th of the month preceding delivery; roll to the next contract 2 business days before that).

- `last_trading_day(year, month)`, `active_month_contract(as_of)`, `active_month_symbol(as_of)`.
- **Verified two independent ways**:
  1. Against the resolution text's own worked example ("25th is a Saturday → LTD = Tuesday the 21st, roll = Friday the 17th") — exact match.
  2. Against Pyth's own live feed metadata: computed `last_trading_day(2026, 9) == 2026-08-20`, which matches Pyth's own feed description for `WTIU6`, *"PYTH WTI 20 AUGUST 2026"*, verbatim.
- As of 2026-08-17 (the date observed live this round), the active contract is **WTIU6** (September 2026 delivery); the roll to `WTIV6` happens the very next day, 2026-08-18 — the golden-case market is currently right at a roll boundary.
- Documented limitation: business-day math is Mon–Fri only, no real CME/NYSE holiday calendar — flagged honestly in the module docstring, not silently assumed exact.

## 5. Tests

10 new tests in `tests/test_pyth_provider.py`, all real, all passing:
- Roll-rule worked example, live-feed cross-check, before/after the Sept→Oct roll boundary, mid-contract stability, expired-contract-is-skipped.
- `fetch_latest_price`: real response shape (captured from the live call above), network error, empty result, malformed payload, negative-price rejection — all mocked (no live network in the test suite itself).

1100/1100 total tests pass (was 1090 before this block). Ruff clean. `git diff --check` clean.

## 6. What this block does NOT do (honest boundary)

- **Not wired into the forecast engine yet.** `quant.py`/`engine.py` are untouched this round. Wiring the current price into a real WTI forecast requires August-high and realized-volatility inputs, which depend on historical OHLC data this round did not verify a source for (see §1). Half-wiring a "current price only, no volatility" pseudo-forecast into the live forecast path was judged too risky to the existing, well-tested engine without those inputs ready — per the master order's own explicit instruction not to fabricate a baseline before its real inputs exist.
- **No mathematical touch probability computed.** Blocks E–T (current price / August high / condition-already-met / realized volatility / the actual touch-probability output / snapshot lineage / product-mode promotion / UI / failure-mode tests / control cases) remain open.
- **CME fallback not audited.**
- **`pythdata.app` (the historical-candle explorer the resolution text names) not investigated.**

## 7. Remaining real blocker

**A verified historical OHLC/candle source for WTI.** Hermes' public API doesn't provide it; the resolution text's own named alternative (`pythdata.app`) has not yet been checked for a usable API. This is the single next dependency before August-high, realized volatility, or a real touch probability can be computed without fabrication.

## 8. Push status

Committed and pushed to `origin/master`; see git log for the exact commit hash.
