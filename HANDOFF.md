# HANDOFF

Checkpoint file per the project owner's steering instruction. Not a roadmap — update after coherent milestones only.

## CURRENT HEAD
`efb5c4e` (pushed to origin/master)

## COMPLETED
- Phases A–P of the original mega-brief (semantic proposition parsing, event extraction, evidence relation classification, base rates + extraordinary-event guard + divergence suppression, market classification taxonomy, historical backfill + weighted comparable baseline with Wilson intervals, specialized models geopolitics/macro/politics/quant(real CoinGecko)/sports(honestly unavailable) routed via a real event_type detector, quality-weighted market-blind ensemble, mocked-only LLM semantic assist (off by default), structured event persistence, real data_quality/confidence composites, prior provenance tagging, negation-robust semantics, itemized divergence red-team audit (PASS/WARN/REJECT), shadow forecast snapshot persistence with look-ahead protection, calibration-metrics framework (UNCALIBRATED), frontend explainability).
- 20-market acceptance run: 13/20 real independent forecasts, reported honestly (informal 15/20 target not hit, no fabrication).
- Qwen's parallel work (now merged, Qwen paused): Provider Health tracking (real, wired into scan pipeline, `provider_health` table), Data Gap Engine (`data_gaps.py`), Causal Distance framework (`288c74e` — audit not yet done by Claude), claim extraction/verification module (`claims.py`) — now CONNECTED to evidence.py's real evidence-scoring path (persists claims/claim_groups with stable sha256 ids, real dedup via `group_claims_by_normalization`), 3 real bugs found+fixed in the process (missing dataclass fields, broken `.normalized()` call, unstable hash-based ids).
- Hormuz regression test (`tests/test_hormuz_regression.py`) — real current pipeline output checked first (no divergence currently produced, evidence too thin), test locks in correct suppress/audit behavior rather than hardcoding the historical 4.5%/47% pair.
- Market-blindness re-verified: history-only, quant-routed, politics-routed, and evidence/claims-connected routes all confirmed byte-identical independent_probability across synthetic market prices 5/20/50/80/95%.
- `pyproject.toml` testpaths fixed so root `pytest` doesn't try to collect Qwen's live-network ad-hoc scripts in `scripts/`.

## IN PROGRESS
Nothing actively running right now (main session between dispatches).

## OPEN (per steering instruction's Definition of Done, point 21)
- Audit Qwen's `data_gaps.py`/causal-distance module for real: what's SCHEMA_ONLY/SCAFFOLD/IMPLEMENTED/CONNECTED/LIVE_VERIFIED — is Data Gap output actually consumed by engine.py, or just computed and unused?
- World State / Path-to-Resolution concept (steering point 9/21) — not yet built as an explicit module; may partially overlap with existing MarketProposition/yes_condition/no_condition — needs an honest audit before building anything new.
- Forecast Maturity levels (MATURE_FORECAST/SUPPORTED_FORECAST/PARTIAL_FORECAST/HYPOTHESIS/CONTEXT_ONLY/NO_FORECAST) — new required taxonomy from steering point 14, not yet implemented anywhere.
- Real 30-market acceptance run (steering point 13/14) — only 20 done so far, and without forecast_maturity/world_state/counter-evidence-count columns since those don't exist yet.
- Model-usage proof across the 30 markets (steering point 15) — eligible/available/actually_used/mean_weight/data_source per model.
- Counter-evidence tracking as a distinct concept from "rejected evidence" — check if this already exists implicitly.
- Full browser walkthrough incl. Hormuz market specifically (steering point 17) — last browser walkthrough (Phase P) covered Trump/Putin/BTC but not Hormuz.
- Final secrets check + git-clean check before any "COMPLETE" claim.

## NEXT ACTION
Audit `src/polymarketpulse/data_gaps.py` and whatever else `288c74e` built (grep for causal_distance/world_state modules), classify honestly per SCHEMA_ONLY/SCAFFOLD/IMPLEMENTED/CONNECTED/LIVE_VERIFIED, then design+implement Forecast Maturity levels since the 30-market acceptance table requires that field to exist first.

## TEST STATUS
700 passed, 0 failed (run via `python -m pytest tests/ -q` — use `tests/` explicitly, not bare `pytest`, since `scripts/*.py` needs a live server). Ruff clean.

## KNOWN BLOCKERS
None currently blocking (no missing secrets, no external-service-only dependency identified yet).
