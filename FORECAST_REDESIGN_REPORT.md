# Forecast Core Redesign

Baseline: `81f3761aa252ca9cc49afa91ddbf5ea4c073728d`  
Date: 2026-08-14

## Decision

**CONDITIONAL GO for the Fed archetype only; no publish or deployment GO.**

The old generic engine could not distinguish a real model from generic
history/evidence/momentum context. A central archetype contract now permits
only validated category-specific models to write a Model Shadow.

## Archetype registry

| Archetype | State | Numeric consequence |
| --- | --- | --- |
| MACRO_POLICY | SHADOW_VALIDATED | Exact Fed targets with fresh official inputs only. |
| LEGISLATIVE_PROCESS | DATASET_BUILDING | Clarity remains research-only. |
| GEOPOLITICAL_STATE_TRANSITION | DATASET_BUILDING | Hormuz remains research-only. |
| PRICE_THRESHOLD | DATASET_BUILDING | No validated model yet. |
| Generic / crypto-regulatory / sports | UNSUPPORTED | `NO_ARCHETYPE`, research-only. |

Generic evidence stays a research input and cannot populate
`model_hypothesis_probability`. The original sources, claims, temporal state,
resolution path, coherence, lineage, queue, evaluation and UI remain reused.

## Fed dataset and model

`fed_fomc_actions_2021_2025@2026.08.14.1` contains 40 scheduled FOMC meetings
from 2021-01-27 to 2025-12-10, with five explicit outcome buckets. The action
lineage is the [Federal Reserve open-market history](https://www.federalreserve.gov/monetarypolicy/openmarket.htm);
scheduled hold rows use the [official FOMC calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm).

The initial interpretable model is a Laplace-smoothed prior-action transition
distribution. It uses no market price and no silently revised macro vintage.
Chronological validation uses 2021-23 train (24) and 2024-25 holdout (16):

| Metric | Unconditional baseline | Transition model |
| --- | ---: | ---: |
| Multiclass log loss | 1.7179 | 1.4864 |
| Multiclass Brier | 0.7072 | 0.6013 |

This is small-sample and regime-sensitive. It validates a historical,
market-price-blind shadow fixture, not a publishable edge claim.

## Live and remaining archetypes

The current Fed market correctly returns `CRITICAL_INPUT_MISSING`: the current
FRED/BLS snapshot was unavailable, and the official action dataset ends in
December 2025. Inferring intervening 2026 FOMC actions from EFFR would be
lookahead-prone. There is therefore no live Fed shadow to claim.

Clarity has an official House `PATH_STEP`, but no historical deadline-aware
House-to-Senate-to-signature transition dataset. Hormuz has a PortWatch state,
but no defensible historical threshold-transition dataset. Both return
`MODEL_NOT_VALIDATED`; old generic 51.82% and 46.10% contexts are not shadows.

Migration 32 adds versioned `forecast_datasets`, `forecast_models` and
immutable `forecast_shadows`. API predictions expose archetype/capability/
reason/diagnostics, and Advanced UI shows them. CLI: `forecast-dataset-report`
and `forecast-model-validate [--persist]`.

Before live activation: collect a vintage-safe official macro panel and current
FOMC-action feed, expand chronological validation, then separately build
legislative and PortWatch transition datasets. No forecast is published.

## 30-market retest

All 30 stored acceptance candidates completed without runtime errors. The
result remains 30 `NO_FORECAST`, zero Model Shadows, zero evidence-backed and
zero published forecasts in live data. This is expected: the only validated
archetype is blocked by its current official-input requirement; Clarity and
Hormuz remain dataset-building. The retest confirms that redesign did not
convert generic research context into a numeric fallback.

Final validation: `1033 passed` (one upstream Starlette/httpx deprecation
warning), Ruff clean, JavaScript syntax clean, diff and secrets scans clean.
Migration 32 reached the same schema fresh and on a current DB copy twice
(idempotent). Browser acceptance showed the Fed detail as `MACRO_POLICY` /
`SHADOW_VALIDATED` with `CRITICAL_INPUT_MISSING` and no published forecast.
