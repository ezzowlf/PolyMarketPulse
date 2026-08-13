# Forecast Intelligence Recovery and Proof

Date: 2026-08-14  
Baseline: `324cf5b38afe46c216f493402511e23f8f7a19a4`

## Decision

**STOP / REDESIGN.** The system now correctly withholds a forecast where it
does not have a target-specific, reproducible probability model. That is a
successful truth-and-safety recovery, but it is not proof that the product
currently produces forecast intelligence for the audited live markets.

The decision is based on a live rerun of 30 stored Polymarket candidates:
`0` Model Shadows, `0` evidence-backed forecasts, and `0` published forecasts.
Two generic-evidence calculations remain visible only as suppressed internal
context (Hormuz `0.4610`, Clarity Act `0.5182`); neither is a calibrated model
for the market's resolution rule and neither is exposed as a Model Shadow.

## Dataflow and persistence audit

| Stage | Verified state | Finding |
| --- | --- | --- |
| Provider market | Stored market, resolution text, market probability and snapshot | The market probability remains distinct from every model field. |
| Research source | Sources, claims and raw source references are persisted | GovTrack and PortWatch primary-source claims are present; GDELT failure is optional discovery degradation when a primary source succeeds. |
| Quantitative input | `macro_observations` supports `source_id` for new records | Older saved observations have no retroactive source id; they cannot be presented as a reproducible fresh macro snapshot. |
| Prediction snapshot | Stores model/evidence/published fields separately | `model_hypothesis_probability` is now populated only by an available numeric domain model, not a history/news/claim ensemble. |
| Publication | Maturity, divergence and evidence gates | No audited live case crossed the publication gate. |

The repair closes two semantic leaks: price-anchored momentum/news/event
relations cannot create `BLENDED_FORECAST` without an independent model, and a
market question cannot be interpreted as a confirmed geopolitical event.

## Golden-case evidence

### Fed exact 25 bp outcome (`polymarket:2252245`)

The resolution is a discrete upper-bound target-range outcome after the
September 15-16 FOMC, rounded to 25 bp. It requires an outcome distribution,
not a directional macro score. Live source checks found NY Fed EFFR available
(`2026-08-12`, `3.63`), but FRED timed out and unkeyed BLS returned its daily
quota response (download endpoints returned `403`). The available NY Fed rate
does not create the required discrete distribution or a complete
point-in-time macro snapshot. Result: `NO_FORECAST`.

### Clarity Act (`polymarket:1163699`)

The rule requires H.R.3633 to pass both chambers and be signed by
2026-12-31. The persisted primary claim is GovTrack `pass_over_house`,
`PATH_STEP`, House vote on `2025-07-17`. That proves the completed House step,
not the probability of Senate passage and presidential signature before the
deadline. The next decisive observation is a Senate action, not another
headline. Result: `NO_FORECAST`; the former generic `0.5182` context is
suppressed and is not a Model Shadow.

### Hormuz (`2774056`)

The rule is a PortWatch seven-day moving-average arrivals threshold of at
least 60 by the deadline. The persisted primary observation is PortWatch's
average `4.43`, far below 60. This is a valid current state observation, but
there is no historical baseline or transition model to estimate reaching 60.
Result: `NO_FORECAST`; the generic `0.4610` context is suppressed and is not a
Model Shadow.

### Cross-market and current-failure checks

The stored cross-market relationships and coherence/lineage records were
retained. The real rerun covered market families including the Fed range
options, Clarity, Hormuz and geopolitical candidates. The prior failure shape
(a market question being treated as evidence of its own confirmed event) now
returns no specialized geopolitical estimate. No cross-market relationship is
used as a substitute for a target-specific probability model.

## Source-health result

| Provider | Live result | Product effect |
| --- | --- | --- |
| NY Fed | EFFR response available | Useful policy-rate input only; insufficient for exact outcome distribution. |
| FRED | Read timeout | Required source unavailable; no fabricated fallback. |
| BLS | Anonymous daily quota response; download `403` | Required inflation/labor snapshot unavailable. |
| GovTrack | Primary legislative claim available | Research remains usable despite optional discovery failure. |
| IMF PortWatch | Current Hormuz observation available | Research remains usable; does not imply transition probability. |
| GDELT | Discovery failures observed | Classified `DISCOVERY_DEGRADED` when a required primary source succeeded; it no longer falsely marks primary research unreachable. |

## Required replacement before a GO decision

1. A vintage-safe Fed 25-bp outcome-distribution model with official FOMC,
   target-range and macro inputs, plus held-out calibration.
2. A legislative conditional transition model for House-to-Senate-to-signature
   paths, with historically evaluated probabilities and deadline features.
3. A PortWatch state-transition model with a documented normalization baseline,
   historical transitions and calibration.
4. Explicit input-snapshot lineage for every numeric model input; old
   unlineaged macro rows must remain non-reproducible rather than silently
   promoted.

Until those models and tests exist, the correct UI/API behavior is an honest
`NO_FORECAST`, not a heuristic number presented as an independent forecast.

## Validation record

Final run: `1025 passed` (one upstream Starlette/httpx deprecation warning),
Ruff clean, JavaScript syntax clean, `git diff --check` clean, secrets scan
clean, and fresh/current-copy migration runs both reached schema version 31
idempotently. Browser acceptance on the final local server confirmed the Fed,
Clarity and Hormuz pages display no published forecast and that the dashboard,
API and refreshed DB state agree on zero current Model Shadows.
