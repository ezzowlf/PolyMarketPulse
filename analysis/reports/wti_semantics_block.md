# WTI $85 Golden Case — Block A-D (Semantics + Routing)

Market: `polymarket:3310013` — "Will WTI Crude Oil (WTI) hit (HIGH) $85 in August?"

## 1. WTI Semantics: before/after

| Field | Before | After |
|---|---|---|
| `event_type` | `None` | `price_above` |
| `asset` | `None` | `WTI_CRUDE_OIL` |
| `asset_class` | (field didn't exist) | `COMMODITY` |
| `threshold` | `None` | `85.0` |
| `unit` | `None` | `USD` |
| `barrier_field` | (field didn't exist) | `HIGH` |
| `deadline` | `None` | `August` |
| `deadline_semantics` | `None` | `by_deadline` (barrier/touch) |
| `price_contract_type` | (field didn't exist) | `TOUCH_HIGH` |
| `proposition_status` | `AMBIGUOUS` | `CLEAR` |

Root causes fixed (all in `src/polymarketpulse/prediction/semantics.py`):
1. No commodity/futures asset taxonomy existed (`_ASSET_ALIASES` was crypto-only). Added `_COMMODITY_ASSET_ALIASES`/`detect_commodity_asset()`, combined via `_detect_any_priced_asset()`.
2. `_THRESHOLD_PATTERN` required the number directly after the direction verb; the real question's `"hit (HIGH) $85"` phrasing breaks that. Extended the regex to tolerate an optional `(HIGH)`/`(LOW)` annotation, captured as the new `barrier_field`.
3. `_DEADLINE_PATTERN` only recognized `"by <date>"`; the real question uses `"in August"`. Added `_IN_MONTH_PATTERN` as a lower-precedence fallback, defaulting to barrier/touch semantics (the natural reading of "in a period").
4. Added `_TOUCH_VERBS` ("hit"/"touch"/"reach") as an explicit override: these verbs mean barrier/touch semantics regardless of date preposition, matching real English usage — "hit $85 by Aug 31" and "hit $85 in August" both mean "at any point," never "at the deadline instant."
5. New explicit `price_contract_type` vocabulary (`TOUCH_HIGH`/`TOUCH_LOW`/`ABOVE_AT_DEADLINE`/`BELOW_AT_DEADLINE`), a strict function of `deadline_semantics` + `direction` + `barrier_field` — never independently guessed.

All fixes are general parsing rules driven by question/resolution text, not a market-ID special case (per Block A's explicit requirement).

## 2. Provider chosen (Block E)

**None yet.** This block deliberately stopped before building a data provider. `quant.py`'s existing barrier/first-passage math (`_SUPPORTED_ASSETS`) remains crypto-only (CoinGecko). No Pyth/CME integration was attempted this round — Pyth's public Hermes API was not verified live (no network access exercised in this session); that verification is the explicit next step, not assumed working.

## 3. Current WTI input / August high / volatility / touch probability

**Not available.** No real point-in-time WTI price data exists in this codebase yet (confirmed, unchanged from the prior session's finding). No numbers are fabricated here.

## 4. Polymarket probability

66.5% (observed at test time, from the real `outcome_prices` column, `polymarket:3310013`).

## 5. Already reached $85?

**Not evaluated** — no price data source exists to check this against.

## 6. Browser result

Confirmed in the browser against the real production DB: the market detail page's "Was fehlt" (missing) section now reads:

> Modell-Input nicht verfügbar: MODEL_NOT_VALIDATED

instead of the previous generic NO_ARCHETYPE/AMBIGUOUS state. This is Block D/N's "Option B" outcome: a clear, specific blocker ("model input not validated" — i.e. semantics are understood, but no validated numeric model/data exists yet) rather than "semantics failed to parse." No console errors observed.

End-to-end trace (`compute_prediction` → `route_archetype`): `forecast_archetype = "PRICE_THRESHOLD"`, `archetype_capability_state = "DATASET_BUILDING"`, `numeric_model_reason_code = "MODEL_NOT_VALIDATED"` — this exact intermediate state (real archetype recognized, model honestly not yet validated) already existed as pre-built infrastructure (`archetypes.py`'s `REGISTRY["PRICE_THRESHOLD"]`) from an earlier round; this session's semantics fix is what activates it correctly for WTI. `data_coverage.py`'s `next_research_action` still reports a generic `NO_ARCHETYPE` reason (out of scope for this round — `INPUT_CONTRACTS` doesn't yet cover `PRICE_THRESHOLD`, a known, previously-documented scope limit), so there is a small remaining inconsistency between the raw prediction layer (now correctly PRICE_THRESHOLD-aware) and the VOI/coverage layer (still says NO_ARCHETYPE) — flagged honestly, not fixed this round.

## 7. Tests

8 new regression tests, all real, all passing:
- `tests/test_semantics.py`: 6 tests (full WTI parse, TOUCH_LOW variant, touch-vs-terminal distinction, BELOW_AT_DEADLINE, touch-verb override, commodity-mention-without-threshold stays non-price).
- `tests/test_specialized_models.py`: 2 tests (routes to quant model + honest "not supported" unavailability; reaches PRICE_THRESHOLD/DATASET_BUILDING/MODEL_NOT_VALIDATED end-to-end).

1090/1090 total tests pass (was 1082 before this block). Ruff clean. `git diff --check` clean.

## 8. Remaining real blocker

**No commodity/futures price data provider.** This is the single, well-isolated next dependency for everything downstream (Blocks E-O: point-in-time snapshots, current price, August high, volatility, the mathematical touch baseline, and eventually training/calibration). Per the master order's own priority list, this must be resolved — with a real, verified, legally-accessible data source — before any of that downstream work begins.

## 9. Push status

Committed and pushed to `origin/master` after this report; see git log for the exact commit.
