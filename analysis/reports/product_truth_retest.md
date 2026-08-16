# Product Truth Retest -- 22-Market Sample

Generated: 2026-08-16T16:35:27.495088+00:00

## Counts

- VALIDATED_NUMERIC_FORECAST: 2
- STRUCTURED_OUTLOOK: 5
- INSUFFICIENT_DATA: 15
- USER_VALUE_YES: 7
- USER_VALUE_NO: 15
- NO_ARCHETYPE (of the INSUFFICIENT_DATA cases): 15

Reference (prior audit, before this round's fixes): 3 VALIDATED_NUMERIC_FORECAST / 3 STRUCTURED_OUTLOOK / 16 INSUFFICIENT_DATA.

## Real bug found and fixed this round

**structured_state.py `_current_state_summary()`**: wrapped the honest `WaterwayHealthState.current_state == "UNKNOWN"` sentinel into the prose sentence `"Waterway-Status: UNKNOWN."`. `product_mode.py`'s `_has_real_structure()` only exact-matched the literal string `"UNKNOWN"`, so the wrapped sentence silently passed as "real content" and promoted a market with ZERO real waterway evidence to STRUCTURED_OUTLOOK. Confirmed live on `polymarket:2176262` ("US announces end of Iranian blockade..."), which flipped from a phantom STRUCTURED_OUTLOOK to the honest INSUFFICIENT_DATA/NO_ARCHETYPE after the fix. Fixed by skipping the wrapping branch entirely when the sentinel is UNKNOWN (falls through to the honest evidence-count fallback, or None if nothing real exists at all). 2 new regression tests added (`tests/test_structured_state.py`).

## Per-market detail

| market_id | category | product_mode | coverage | action | user_value | reason |
|---|---|---|---|---|---|---|
| polymarket:2252245 | CENTRAL_BANKS | VALIDATED_NUMERIC_FORECAST | 1/1 | NONE | YES | COVERAGE_COMPLETE |
| polymarket:1163699 | LEGISLATION | STRUCTURED_OUTLOOK | 4/4 | NONE | YES | COVERAGE_COMPLETE |
| 2774056 | WAR_PEACE | STRUCTURED_OUTLOOK | 4/4 | NONE | YES | COVERAGE_COMPLETE |
| polymarket:3399426 | WAR_PEACE | STRUCTURED_OUTLOOK | 2/4 | FETCH | YES | CRITICAL_INPUT_MISSING:primary_measurement_source |
| polymarket:561974 | ELECTIONS | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3128889 | POLITICS | STRUCTURED_OUTLOOK | 2/4 | FETCH | YES | CRITICAL_INPUT_MISSING:primary_measurement_source |
| polymarket:3474245 | CRYPTO | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3254056 | SPORT_TENNIS | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:2176262 | OTHER | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3145682 | SOCIAL | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:2252243 | CENTRAL_BANKS | VALIDATED_NUMERIC_FORECAST | 1/1 | NONE | YES | COVERAGE_COMPLETE |
| polymarket:2063134 | GEOPOLITICS | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3145767 | SPORT_BASKETBALL | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3096416 | ENERGY | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3239538 | FINANCIAL_MARKETS | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3253865 | LEGISLATION | STRUCTURED_OUTLOOK | n/a | NONE | YES | NO_ARCHETYPE |
| polymarket:3242103 | ENTERTAINMENT | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:2772194 | SPORT_FOOTBALL | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3295873 | SPORT_OTHER | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3275655 | SPORT_TENNIS | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| polymarket:3370489 | SPORT_OTHER | INSUFFICIENT_DATA | n/a | NONE | NO | A |
| 3286340 | SPORT_OTHER | INSUFFICIENT_DATA | n/a | NONE | NO | A |

## Known scope limitation (not a bug, documented)

`polymarket:3253865` (SPORTS category) is STRUCTURED_OUTLOOK (real resolution-path template with 4 defined steps) but `data_coverage.py` reports `archetype: None` because `INPUT_CONTRACTS` only covers MACRO_POLICY/LEGISLATION/GEOPOLITICS by deliberate, documented design. This means the market-detail UI would show a real "Strukturierte Einschätzung" panel next to the honest "kein unterstützter Analyse-Archetyp" Datenabdeckung panel for the same market -- not incorrect, but a UX inconsistency worth widening the VOI contract set to cover in a future round (would need a real SPORTS input contract, not attempted this round -- out of scope).