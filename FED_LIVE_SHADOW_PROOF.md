# Fed live shadow proof

Generated 2026-08-14 UTC against the local production-data database.

## Decision

**GO FOR VPS SHADOW.** A live, numeric, unpublished Fed shadow was produced
from an official Federal Reserve input. This is not a publication or trading
approval.

## Selected market and semantics

- Market: `polymarket:2252244` — “Will there be no change in Fed interest
  rates after the September 2026 meeting?”
- Market snapshot at generation: 70.50% YES.
- Exact YES bucket: `UNCHANGED`; meeting date: 2026-09-16.
- Deadline: 2026-09-16; resolution mechanism: official rate announcement.
- Raw resolution text is not stored by the provider, so that limitation stays
  visible in the regular resolution-quality audit. It does not change the
  exact, unambiguous outcome wording selected for this shadow.

## Live input and lineage

The sole validated model feature is `previous_fomc_action`; no market price,
CPI, unemployment, EFFR, or text sentiment is a model feature. The live
feature was extracted from the official July 29 statement:

`Federal Reserve calendar -> July 29, 2026 statement -> UNCHANGED ->
previous_fomc_action -> fed_prior_action_transition -> forecast shadow`.

- Provider: Federal Reserve Board.
- Raw statement: <https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm>
- Normalized action/date: `UNCHANGED`, 2026-07-29.
- Current target range: 3.50%–3.75% (percent, not basis points).
- Retrieved at: 2026-08-14T00:30:53Z.
- Stored immutable shadow: `forecast_shadows.id=5`, linked prediction
  snapshot `14021`.

Provider health was checked before the live proof. NY Fed returned EFFR 3.63%
for 2026-08-12. The anonymous BLS endpoint returned its explicit daily
anonymous-request threshold response, and the BEA sample key is inactive;
FRED was unavailable in this environment. They are not hidden or substituted:
they are **not model features** of this validated version, so none is used to
generate the probability.

## Model, result, and validation

- Model/dataset: `fed_prior_action_transition` / `fed_fomc_actions_2021_2025`;
  version `2026.08.14.1`.
- Training/holdout: 24 scheduled decisions (2021–2023) / 16 later decisions
  (2024–2025).
- Baseline/model log loss: 1.7179 / 1.4864.
- Baseline/model multiclass Brier: 0.7072 / 0.6013.
- Validation: pass, with a small-sample warning (40 total decisions).
- Shadow confidence: 42.0/100; the low confidence is intentional and reflects
  the small, one-feature dataset.
- Selected-contract shadow: **71.43% YES**; published probability: `null`.

Meeting-family distribution from that one input is:

| Outcome | Probability |
| --- | ---: |
| Cut 50+ bp | 7.14% |
| Cut 25 bp | 7.14% |
| Unchanged | 71.43% |
| Hike 25 bp | 10.71% |
| Hike 50+ bp | 3.57% |

The probabilities are non-negative and sum to exactly 1.0. The second real
contract, `polymarket:2252245` (September +25 bp), projected the same family
distribution to 10.71% YES while its stored market price was 28.50%.

## Reproducibility and safeguards

Re-running the model twice from the same persisted policy input produced an
identical distribution and selected probability. The shadow record is append
only; it stores the market snapshot, model probability, model confidence,
dataset/model version, full input diagnostic, validation metrics, and raw
source lineage. The price is retained only for comparison and is not passed to
the model.

Counterfactual CPI and labour-market tests are not applicable to this model:
they are deliberately excluded features, so claiming a directional response
would fabricate behaviour that has not been trained or validated.

## API/UI acceptance

The current API returned the same stored/live values: market 70.50%, shadow
71.43%, published `null`, `MACRO_POLICY`, `SHADOW_VALIDATED`, confidence 42.0,
and the raw Fed statement URL. The UI now renders distinct `MARKT`, `INTERNES
MODELL`, and `STATUS: Shadow - noch nicht veröffentlicht` cards.

The in-app browser was unable to navigate either `127.0.0.1:8027` or
`localhost:8027`, both with `net::ERR_BLOCKED_BY_CLIENT`; an alternate Chrome
surface was not available. This is a browser-runtime limitation, not an API or
application response. Browser visual acceptance therefore remains explicitly
unverified in this environment.
