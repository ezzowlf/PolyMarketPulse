from __future__ import annotations

import time
from datetime import UTC, datetime

from pydantic import ValidationError as PydanticValidationError

from ..config import Settings
from ..prediction import PREDICTION_VERSION, PredictionResult, compute_prediction
from ..storage import Storage
from .cache import hash_payload, lookup
from .client import (
    AIContextError,
    AIDisabledError,
    AIError,
    AIResponseError,
    OpenAIStructuredClient,
)
from .context_builder import build_market_context, context_hash
from .cost import (
    CostEstimate,
    actual_cost,
    estimate_cost,
    estimate_tokens_from_text,
    spent_today_usd,
)
from .fallback import build_fallback_explanation
from .prompts import (
    EXPLANATION_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_ask_prompt,
    build_compare_prompt,
    build_explain_market_prompt,
    build_explain_recommendation_input,
    build_explain_signal_prompt,
    build_news_analysis_prompt,
)
from .schemas import (
    DISCLAIMER,
    EXPLANATION_PROMPT_VERSION,
    PROMPT_VERSION,
    AIAnalysisResponse,
    AIRunMeta,
    AnalysisResult,
    ExplainRecommendationResponse,
    ExplanationResult,
    ExplanationRunMeta,
)
from .status import (
    AI_STATUS_BLOCKED_COST_LIMIT,
    AI_STATUS_BLOCKED_DAILY_BUDGET,
    AI_STATUS_BLOCKED_INPUT_TOKEN_LIMIT,
    AI_STATUS_DISABLED,
    AI_STATUS_INCONSISTENT_WITH_ENGINE,
    AI_STATUS_REPAIR_FAILED,
    AI_STATUS_SUCCESS,
    ModelAttempt,
    sum_optional,
)
from .validation import ValidationError, validate_explanation


def _require_ready(settings: Settings) -> None:
    if not settings.ai_enabled:
        raise AIDisabledError("AI is disabled (POLYMARKETPULSE_AI_ENABLED=false)")
    if not settings.openai_api_key:
        raise AIDisabledError("AI is enabled but no OPENAI_API_KEY is configured")


def _build_client(settings: Settings) -> OpenAIStructuredClient:
    return OpenAIStructuredClient(
        api_key=settings.openai_api_key,  # type: ignore[arg-type]
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        max_output_tokens=settings.openai_max_output_tokens,
    )


def _execute(
    storage: Storage,
    settings: Settings,
    analysis_type: str,
    market_id: str | None,
    key_hash: str,
    user_prompt: str,
    client: OpenAIStructuredClient | None = None,
) -> AIAnalysisResponse:
    cached = lookup(
        storage, analysis_type, settings.openai_model, PROMPT_VERSION, key_hash, settings.ai_cache_ttl_seconds
    )
    if cached is not None:
        result = AnalysisResult.model_validate_json(cached["response_json"])
        result = result.model_copy(update={"disclaimer": DISCLAIMER})
        return AIAnalysisResponse(
            result=result,
            meta=AIRunMeta(
                analysis_id=cached["id"],
                model=settings.openai_model,
                prompt_version=PROMPT_VERSION,
                cached=True,
                input_tokens=cached["input_tokens"],
                output_tokens=cached["output_tokens"],
                created_at=cached["created_at"],
            ),
        )

    client = client or _build_client(settings)
    started = time.monotonic()
    try:
        parsed, input_tokens, output_tokens = client.generate_structured(
            SYSTEM_PROMPT, user_prompt, AnalysisResult, "market_analysis"
        )
        result = AnalysisResult.model_validate(parsed)
        # Strict-mode Structured Outputs requires every field (including
        # `disclaimer`) to be model-generated rather than defaulted, so the
        # model sometimes writes its own wording here. Overwrite with the
        # canonical, compliance-reviewed text so it's never left to chance.
        result = result.model_copy(update={"disclaimer": DISCLAIMER})
    except AIResponseError:
        duration_ms = int((time.monotonic() - started) * 1000)
        storage.record_ai_run(
            analysis_type, market_id, settings.openai_model, PROMPT_VERSION, key_hash,
            status="error", duration_ms=duration_ms, input_tokens=None, output_tokens=None,
            cached=False, error_code="response_error", response_json=None,
        )
        raise
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        storage.record_ai_run(
            analysis_type, market_id, settings.openai_model, PROMPT_VERSION, key_hash,
            status="error", duration_ms=duration_ms, input_tokens=None, output_tokens=None,
            cached=False, error_code=type(exc).__name__, response_json=None,
        )
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    run_id = storage.record_ai_run(
        analysis_type, market_id, settings.openai_model, PROMPT_VERSION, key_hash,
        status="completed", duration_ms=duration_ms, input_tokens=input_tokens, output_tokens=output_tokens,
        cached=False, error_code=None, response_json=result.model_dump_json(),
    )
    return AIAnalysisResponse(
        result=result,
        meta=AIRunMeta(
            analysis_id=run_id,
            model=settings.openai_model,
            prompt_version=PROMPT_VERSION,
            cached=False,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            created_at=datetime.now(UTC).isoformat(),
        ),
    )


def explain_market(
    storage: Storage, settings: Settings, market_id: str, client: OpenAIStructuredClient | None = None
) -> AIAnalysisResponse:
    _require_ready(settings)
    context = build_market_context(storage, market_id)
    if context is None:
        raise AIContextError(f"Market '{market_id}' not found")
    key_hash = context_hash(context)
    prompt = build_explain_market_prompt(context)
    return _execute(storage, settings, "explain_market", market_id, key_hash, prompt, client)


def explain_signal(
    storage: Storage, settings: Settings, signal_id: int, client: OpenAIStructuredClient | None = None
) -> AIAnalysisResponse:
    _require_ready(settings)
    row = storage.connection.execute(
        """
        SELECT rs.id, rs.provider, rs.provider_market_id, rs.signal_type, rs.score, rs.reasons,
               rs.captured_at, rs.status, m.market_id
        FROM research_signals rs
        LEFT JOIN markets m ON m.provider = rs.provider AND m.provider_market_id = rs.provider_market_id
        WHERE rs.id = ?
        """,
        (signal_id,),
    ).fetchone()
    if row is None or row[8] is None:
        raise AIContextError(f"Signal '{signal_id}' not found or its market is not stored")
    market_id = row[8]
    signal = {
        "id": f"signal:{row[0]}",
        "provider": row[1],
        "signal_type": row[3],
        "score": row[4],
        "reasons": row[5],
        "captured_at": row[6],
        "status": row[7],
    }
    context = build_market_context(storage, market_id)
    if context is None:
        raise AIContextError(f"Market for signal '{signal_id}' not found")
    key_hash = context_hash(context) + ":" + hash_payload(signal)
    prompt = build_explain_signal_prompt(context, signal)
    return _execute(storage, settings, "explain_signal", market_id, key_hash, prompt, client)


def analyze_news_for_market(
    storage: Storage, settings: Settings, market_id: str, client: OpenAIStructuredClient | None = None
) -> AIAnalysisResponse:
    _require_ready(settings)
    context = build_market_context(storage, market_id)
    if context is None:
        raise AIContextError(f"Market '{market_id}' not found")
    if not context.relevant_news:
        raise AIContextError(f"No stored news linked to market '{market_id}'")
    key_hash = context_hash(context)
    prompt = build_news_analysis_prompt(context)
    return _execute(storage, settings, "analyze_news", market_id, key_hash, prompt, client)


def compare_markets(
    storage: Storage, settings: Settings, market_ids: list[str], client: OpenAIStructuredClient | None = None
) -> AIAnalysisResponse:
    _require_ready(settings)
    if len(market_ids) < 2:
        raise AIContextError("compare_markets requires at least two market IDs")

    contexts = []
    for market_id in market_ids:
        context = build_market_context(storage, market_id)
        if context is None:
            raise AIContextError(f"Market '{market_id}' not found")
        contexts.append(context)

    # Only proceed if at least one confirmed cross-provider match connects
    # the requested markets — never let the model compare arbitrary,
    # unrelated markets as if they were the same question.
    placeholders = ",".join("?" for _ in market_ids)
    confirmed = storage.connection.execute(
        f"""
        SELECT COUNT(*) FROM market_matches mm
        JOIN markets ma ON ma.provider = mm.provider_a AND ma.provider_market_id = mm.provider_market_id_a
        JOIN markets mb ON mb.provider = mm.provider_b AND mb.provider_market_id = mm.provider_market_id_b
        WHERE mm.status = 'confirmed' AND ma.market_id IN ({placeholders}) AND mb.market_id IN ({placeholders})
        """,
        (*market_ids, *market_ids),
    ).fetchone()[0]
    if confirmed == 0:
        raise AIContextError(
            "No confirmed cross-provider match connects the given markets; "
            "only status='confirmed' matches may be compared"
        )

    key_hash = hash_payload([context_hash(c) for c in contexts])
    prompt = build_compare_prompt(contexts)
    return _execute(storage, settings, "compare_markets", None, key_hash, prompt, client)


def ask_research_question(
    storage: Storage,
    settings: Settings,
    question: str,
    market_id: str | None = None,
    client: OpenAIStructuredClient | None = None,
) -> AIAnalysisResponse:
    _require_ready(settings)
    context = None
    if market_id is not None:
        context = build_market_context(storage, market_id)
        if context is None:
            raise AIContextError(f"Market '{market_id}' not found")
    key_hash = hash_payload(question, context_hash(context) if context else None)
    prompt = build_ask_prompt(question, context)
    return _execute(storage, settings, "ask_question", market_id, key_hash, prompt, client)


# --- Phase 7: statistics-first prediction + GPT-5-nano explanation --------

_ANALYSIS_TYPE_EXPLAIN_RECOMMENDATION = "explain_recommendation"


def _load_market_row(storage: Storage, market_id: str) -> dict | None:
    row = storage.connection.execute(
        """
        SELECT market_id, provider, provider_market_id, question, category, resolution_source,
               end_date, last_seen_at
        FROM markets WHERE market_id = ?
        """,
        (market_id,),
    ).fetchone()
    if row is None:
        return None
    cols = ("market_id", "provider", "provider_market_id", "question", "category",
            "resolution_source", "end_date", "last_seen_at")
    return dict(zip(cols, row, strict=True))


def _latest_snapshot(storage: Storage, market_id: str) -> tuple[float | None, float, str | None]:
    row = storage.connection.execute(
        "SELECT yes_price, liquidity, captured_at FROM market_snapshots WHERE market_id = ? "
        "ORDER BY captured_at DESC LIMIT 1",
        (market_id,),
    ).fetchone()
    if row is None:
        return None, 0.0, None
    return row[0], row[1] or 0.0, row[2]


def _latest_dq_score(storage: Storage, provider: str, provider_market_id: str) -> float | None:
    row = storage.connection.execute(
        "SELECT score FROM data_quality_reports WHERE provider = ? AND provider_market_id = ? "
        "ORDER BY captured_at DESC LIMIT 1",
        (provider, provider_market_id),
    ).fetchone()
    return row[0] if row else None


def _news_stats(storage: Storage, provider: str, provider_market_id: str) -> tuple[int, float | None]:
    row = storage.connection.execute(
        "SELECT COUNT(*), AVG(confidence) FROM news_market_links WHERE provider = ? AND provider_market_id = ?",
        (provider, provider_market_id),
    ).fetchone()
    return (row[0] or 0), row[1]


def _as_percent(value: float | None) -> float | None:
    return round(value * 100, 1) if value is not None else None


def _build_recommendation_payload(market: dict, prediction: PredictionResult, allowed_source_ids: list[str]) -> dict:
    """Compact, bounded input for GPT-5 nano — deliberately minimal (market
    question, own probability, edge, confidence, deadline phase, top
    factors, scenarios, warnings) rather than a full data dump. Less input
    means less reasoning effort and a lower chance of exhausting the
    output-token budget before a final answer is written. No raw tables,
    no historical row dumps — GPT only ever explains, never computes.

    Every probability-shaped value is sent as an explicit 0-100 percent
    under a `_percent`-suffixed key — never a bare 0-1 fraction — mirroring
    exactly the field names and scale GPT is expected to echo back in
    `probability_explanation` (see ai/schemas.py, ai/prompts.py's example)."""
    key_factors = [
        {"factor": note, "source_ids": [f"reasoning_{i}"]} for i, note in enumerate(prediction.reasoning_notes)
    ][:5]
    scenarios = prediction.scenarios
    data_gaps = [] if prediction.comparable_sample_size >= 5 else ["Zu wenige historische Vergleichsfälle"]
    return {
        "task": "Erkläre kurz die bereits berechnete Prognose.",
        "language": "de",
        "market_question": market["question"],
        "market_yes_percent": _as_percent(prediction.market_yes_probability),
        "estimated_yes_percent": _as_percent(prediction.estimated_yes_probability),
        "estimated_no_percent": _as_percent(prediction.estimated_no_probability),
        "confidence_percent": round(prediction.confidence_score, 1),
        "net_edge_percentage_points": _as_percent(prediction.net_yes_edge),
        "deadline_phase": prediction.deadline_phase,
        "recommendation": prediction.recommendation,
        "key_factors": key_factors,
        "scenarios": {
            "base": scenarios.base_case if scenarios else None,
            "bull": scenarios.bull_case[:2] if scenarios else [],
            "bear": scenarios.bear_case[:2] if scenarios else [],
        },
        "data_gaps": data_gaps,
        "allowed_source_ids": allowed_source_ids,
    }


def _persist_and_wrap(
    storage: Storage,
    settings: Settings,
    model: str,
    market_id: str,
    key_hash: str,
    prediction: PredictionResult,
    explanation: ExplanationResult,
    duration_ms: int,
    cached: bool,
    used_fallback: bool,
    fallback_reason: str | None,
    requested_model: str,
    final_status: str,
    attempts: list[ModelAttempt],
) -> ExplainRecommendationResponse:
    """Persists the run *and* every individual call attempt (see
    ai/status.py::ModelAttempt / ai_model_attempts). Totals are computed
    from the attempts themselves via `sum_optional`, so a fallback caused
    by a rejected-but-real response still records the real usage/cost that
    was incurred — the exact gap a live smoke test exposed."""
    total_input_tokens = sum_optional([a.input_tokens for a in attempts])
    total_output_tokens = sum_optional([a.output_tokens for a in attempts])
    total_estimated_cost_usd = sum_optional([a.estimated_cost_usd for a in attempts])
    total_actual_cost_usd = sum_optional([a.actual_cost_usd for a in attempts])
    total_attempts = sum(1 for a in attempts if a.actual_model is not None)
    repair_attempted = any(a.is_repair and a.actual_model is not None for a in attempts)

    run_id = storage.record_ai_run(
        _ANALYSIS_TYPE_EXPLAIN_RECOMMENDATION, market_id, model, EXPLANATION_PROMPT_VERSION, key_hash,
        status="completed", duration_ms=duration_ms, input_tokens=total_input_tokens, output_tokens=total_output_tokens,
        cached=cached, error_code=None if used_fallback is False else final_status,
        response_json=explanation.model_dump_json(),
        cached_input_tokens=0 if total_input_tokens is not None else None,
        estimated_cost_usd=total_estimated_cost_usd, actual_cost_usd=total_actual_cost_usd,
        requested_model=requested_model, final_status=final_status, total_attempts=total_attempts,
        repair_attempted=repair_attempted, total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens, total_estimated_cost_usd=total_estimated_cost_usd,
        total_actual_cost_usd=total_actual_cost_usd,
    )
    for attempt in attempts:
        storage.record_ai_model_attempt(run_id, attempt)

    return ExplainRecommendationResponse(
        prediction=prediction.as_dict(),
        explanation=explanation,
        meta=ExplanationRunMeta(
            analysis_id=run_id,
            model=model,
            prompt_version=EXPLANATION_PROMPT_VERSION,
            cached=cached,
            duration_ms=duration_ms,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            estimated_cost_usd=total_estimated_cost_usd,
            actual_cost_usd=total_actual_cost_usd,
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
            created_at=datetime.now(UTC).isoformat(),
        ),
    )


def _persist_prediction_snapshot(storage: Storage, market: dict, prediction: PredictionResult) -> int:
    """Every computed prediction is stored for later evaluation
    (evaluation.py), independent of whether GPT was ever called — this is
    what lets accuracy/precision/recall/Brier/log-loss/calibration/edge/ROI
    be measured against the engine alone, not just AI-explained runs. Also
    the reproducibility record for the shadow-trading layer: returns the
    snapshot id so a shadow_trades row can reference exactly which snapshot
    it was decided from."""
    import json

    ie = prediction.independent_evidence
    re = prediction.resolution_edge
    rel = prediction.market_reliability
    risk = prediction.manipulation_risk
    ob = prediction.orderbook_metrics
    flow = prediction.trade_flow_metrics
    wallet = prediction.wallet_concentration
    lag = prediction.reaction_lag

    submodel_json = json.dumps([
        {"name": s.name, "estimated_yes_probability": s.estimated_yes_probability, "weight": s.weight, "available": s.available}
        for s in prediction.submodel_estimates
    ])
    warnings_json = json.dumps(list(prediction.reasoning_notes[:10]))
    config_hash = hash_payload(PREDICTION_VERSION, "v1-config")

    # Phase N: which submodels actually contributed to THIS forecast (source
    # names of contribution_breakdown entries with available=True). Purely
    # derived from prediction.contribution_breakdown, which is itself
    # computed with no resolution/outcome data — no look-ahead risk.
    models_used = ",".join(
        entry.source for entry in prediction.contribution_breakdown if entry.available
    )
    divergence_verdict = (
        prediction.divergence_audit.verdict if prediction.divergence_audit else None
    )

    return storage.save_prediction_snapshot(
        market_id=prediction.market_id, provider=market["provider"],
        provider_market_id=market["provider_market_id"], category=market["category"],
        prediction_version=PREDICTION_VERSION, market_yes_probability=prediction.market_yes_probability,
        estimated_yes_probability=prediction.estimated_yes_probability, net_yes_edge=prediction.net_yes_edge,
        confidence_score=prediction.confidence_score, recommendation=prediction.recommendation,
        comparable_sample_size=prediction.comparable_sample_size,
        independent_probability=prediction.independent_probability,
        resolution_clarity=re.resolution_edge_score if re else None,
        market_reliability_score=rel.score if rel else None,
        market_reliability_level=rel.level if rel else None,
        manipulation_risk_score=risk.risk_score if risk else None,
        opportunity_score=None,  # opportunity score is computed one layer up (opportunities.py); not duplicated here
        deadline_phase=prediction.deadline_phase,
        evidence_count=(len(ie.evidence_for_yes) + len(ie.evidence_for_no)) if ie and ie.available else None,
        independent_confirmation_count=ie.confirmation_count if ie else None,
        contradiction_present=ie.contradiction_detected if ie else None,
        orderbook_imbalance=ob.imbalance if ob and ob.available else None,
        net_flow=flow.net_flow_usd if flow and flow.available else None,
        wallet_concentration_score=wallet.concentration_score if wallet and wallet.available else None,
        reaction_lag_hours=lag.reaction_detected_at_hours if lag else None,
        submodel_estimates_json=submodel_json, warnings_json=warnings_json,
        engine_version=PREDICTION_VERSION, config_hash=config_hash,
        # --- Phase N: shadow forecast snapshot fields, forecast-time only ---
        # market_probability_at_forecast is exactly the market price value
        # that was passed INTO compute_prediction() to produce this
        # prediction (prediction.market_yes_probability echoes that same
        # input back) — never derived from, or aware of, any resolution.
        market_probability_at_forecast=prediction.market_yes_probability,
        blended_probability=prediction.blended_probability,
        calibrated_probability=prediction.calibrated_probability,
        confidence_calibration_status=prediction.confidence_calibration_status,
        forecast_status=prediction.forecast_status,
        models_used=models_used or None,
        divergence_verdict=divergence_verdict,
    )


def get_prediction(storage: Storage, market_id: str) -> PredictionResult:
    """Computes and returns just the binding statistical prediction, with no
    AI call at all — used by the plain /prediction endpoint, the CLI
    `predict` command, and the backtest engine."""
    market = _load_market_row(storage, market_id)
    if market is None:
        raise AIContextError(f"Market '{market_id}' not found")

    yes_price, liquidity, _snapshot_captured_at = _latest_snapshot(storage, market_id)
    dq_score = _latest_dq_score(storage, market["provider"], market["provider_market_id"])
    news_count, news_agreement = _news_stats(storage, market["provider"], market["provider_market_id"])

    prediction = compute_prediction(
        storage.connection,
        market_id=market_id,
        provider=market["provider"],
        provider_market_id=market["provider_market_id"],
        category=market["category"],
        market_yes_price=yes_price,
        liquidity=liquidity,
        data_quality_report_score=dq_score,
        news_count=news_count,
        news_agreement=news_agreement,
        resolution_rules_present=bool(market["resolution_source"]),
        question=market["question"] or "",
        resolution_text=market["resolution_source"],
    )
    _persist_prediction_snapshot(storage, market, prediction)
    return prediction


def explain_recommendation(
    storage: Storage,
    settings: Settings,
    market_id: str,
    nano_client: OpenAIStructuredClient | None = None,
    mini_client: OpenAIStructuredClient | None = None,
    force_recompute: bool = False,
) -> ExplainRecommendationResponse:
    """The full 12-step pipeline: load market -> compute the binding
    statistical prediction -> (maybe) call GPT-5 nano only to *explain* it,
    validated against the engine's own numbers -> persist -> return. GPT
    never runs before the prediction exists, and a full, complete
    explanation is always returned even when AI is unavailable."""
    market = _load_market_row(storage, market_id)
    if market is None:
        raise AIContextError(f"Market '{market_id}' not found")

    yes_price, liquidity, snapshot_captured_at = _latest_snapshot(storage, market_id)
    dq_score = _latest_dq_score(storage, market["provider"], market["provider_market_id"])
    news_count, news_agreement = _news_stats(storage, market["provider"], market["provider_market_id"])

    prediction = compute_prediction(
        storage.connection,
        market_id=market_id,
        provider=market["provider"],
        provider_market_id=market["provider_market_id"],
        category=market["category"],
        market_yes_price=yes_price,
        liquidity=liquidity,
        data_quality_report_score=dq_score,
        news_count=news_count,
        news_agreement=news_agreement,
        resolution_rules_present=bool(market["resolution_source"]),
        question=market["question"] or "",
        resolution_text=market["resolution_source"],
    )
    _persist_prediction_snapshot(storage, market, prediction)

    allowed_source_ids = ["market_price", "historical_comparables"] + [
        f"reasoning_{i}" for i in range(len(prediction.reasoning_notes))
    ]

    data_snapshot_version = snapshot_captured_at or "none"
    relevant_source_hash = hash_payload(sorted(allowed_source_ids))
    key_hash = hash_payload(
        market_id, PREDICTION_VERSION, data_snapshot_version, prediction.recommendation,
        relevant_source_hash, EXPLANATION_PROMPT_VERSION, settings.openai_model,
    )

    cached = None
    if not force_recompute:
        cached = lookup(
            storage, _ANALYSIS_TYPE_EXPLAIN_RECOMMENDATION, settings.openai_model, EXPLANATION_PROMPT_VERSION,
            key_hash, settings.ai_cache_ttl_seconds,
        )
    if cached is not None:
        explanation = ExplanationResult.model_validate_json(cached["response_json"])
        # `final_status` is the authoritative signal (added in migration 9).
        # Legacy rows (pre-migration) have final_status = NULL — fall back
        # to the old input_tokens-is-None heuristic for those only, so
        # older cached entries stay interpretable rather than erroring.
        if cached.get("final_status") is not None:
            cached_was_fallback = cached["final_status"] != AI_STATUS_SUCCESS
        else:
            cached_was_fallback = cached["input_tokens"] is None
        return ExplainRecommendationResponse(
            prediction=prediction.as_dict(),
            explanation=explanation,
            meta=ExplanationRunMeta(
                analysis_id=cached["id"], model=settings.openai_model, prompt_version=EXPLANATION_PROMPT_VERSION,
                cached=True, input_tokens=cached["input_tokens"], output_tokens=cached["output_tokens"],
                estimated_cost_usd=cached.get("estimated_cost_usd"), actual_cost_usd=cached.get("actual_cost_usd"),
                used_fallback=cached_was_fallback,
                fallback_reason="Aus Cache übernommen (ursprünglich regelbasierter Fallback)." if cached_was_fallback else None,
                created_at=cached["created_at"],
            ),
        )

    requested_model = settings.openai_model
    attempts: list[ModelAttempt] = []

    def _fallback(reason: str, status: str) -> ExplainRecommendationResponse:
        explanation = build_fallback_explanation(prediction)
        return _persist_and_wrap(
            storage, settings, requested_model, market_id, key_hash, prediction, explanation,
            duration_ms=0, cached=False, used_fallback=True, fallback_reason=reason,
            requested_model=requested_model, final_status=status, attempts=attempts,
        )

    if not settings.ai_ready:
        return _fallback("AI deaktiviert oder kein API-Key konfiguriert", AI_STATUS_DISABLED)

    payload = _build_recommendation_payload(market, prediction, allowed_source_ids)
    user_prompt = build_explain_recommendation_input(payload)

    est_input_tokens = estimate_tokens_from_text(EXPLANATION_SYSTEM_PROMPT + user_prompt)
    est_output_tokens = settings.openai_max_output_tokens
    if est_input_tokens > settings.openai_max_input_tokens:
        return _fallback("Eingabe überschreitet Token-Limit selbst nach Verdichtung", AI_STATUS_BLOCKED_INPUT_TOKEN_LIMIT)

    def _budget_check(model_name: str) -> tuple[bool, str | None, CostEstimate]:
        """Re-checked before *every* attempt (main, repair, fallback-model
        escalation) — not just once up front — so the per-analysis cap
        accounts for cost already incurred by earlier attempts in this
        same call, and the daily budget accounts for both today's prior
        spend and this call's own running total."""
        est = estimate_cost(model_name, est_input_tokens, est_output_tokens)
        already_spent_this_call = sum_optional([a.actual_cost_usd for a in attempts]) or 0.0
        if already_spent_this_call + est.estimated_cost_usd > settings.openai_max_cost_per_analysis_usd:
            return False, AI_STATUS_BLOCKED_COST_LIMIT, est
        spent_today = spent_today_usd(storage.connection)
        if spent_today + already_spent_this_call + est.estimated_cost_usd > settings.openai_daily_budget_usd:
            return False, AI_STATUS_BLOCKED_DAILY_BUDGET, est
        return True, None, est

    def _record_blocked_attempt(attempt_number: int, is_repair: bool, model_name: str, status: str, est: CostEstimate) -> None:
        attempts.append(
            ModelAttempt(
                attempt_number=attempt_number, is_repair=is_repair, requested_model=model_name, actual_model=None,
                status=status, input_tokens=None, output_tokens=None, estimated_cost_usd=est.estimated_cost_usd,
                actual_cost_usd=None, duration_ms=0,
                error_detail="Aufruf nicht gesendet: Kosten-/Tagesbudget hätte überschritten." if status == AI_STATUS_BLOCKED_COST_LIMIT
                else "Aufruf nicht gesendet: Tagesbudget für KI-Analysen erreicht.",
            )
        )

    def _try_model(
        attempt_number: int, is_repair: bool, model_name: str, client: OpenAIStructuredClient | None,
        est: CostEstimate, repair_note: str | None = None,
    ) -> ExplanationResult | None:
        c = client or OpenAIStructuredClient(
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            model=model_name,
            timeout_seconds=settings.openai_timeout_seconds,
            max_output_tokens=settings.openai_max_output_tokens,
            reasoning_effort=settings.openai_reasoning_effort,
        )
        # On a repair attempt, name the concrete violation from the failed
        # attempt instead of blindly repeating the identical prompt — the
        # model is told exactly what was wrong, never asked to re-derive
        # the engine's values itself.
        prompt_for_attempt = (
            f"{user_prompt}\n\nKORREKTUR ERFORDERLICH: {repair_note}. Nutze weiterhin exakt die "
            f"vorgegebenen Engine-Werte, ändere nur das genannte Feld." if repair_note else user_prompt
        )
        started = time.monotonic()
        try:
            parsed, in_tok, out_tok = c.generate_structured(
                EXPLANATION_SYSTEM_PROMPT, prompt_for_attempt, ExplanationResult, "explanation"
            )
        except AIError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            in_tok, out_tok = exc.input_tokens, exc.output_tokens
            cost = actual_cost(model_name, in_tok, 0, out_tok) if (in_tok is not None or out_tok is not None) else None
            attempts.append(
                ModelAttempt(
                    attempt_number=attempt_number, is_repair=is_repair, requested_model=model_name,
                    actual_model=model_name, status=exc.error_code, input_tokens=in_tok, output_tokens=out_tok,
                    estimated_cost_usd=est.estimated_cost_usd, actual_cost_usd=cost, duration_ms=duration_ms,
                    error_detail=str(exc)[:200],
                )
            )
            return None

        duration_ms = int((time.monotonic() - started) * 1000)
        cost = actual_cost(model_name, in_tok or 0, 0, out_tok or 0) if (in_tok is not None or out_tok is not None) else None

        try:
            result = ExplanationResult.model_validate(parsed)
        except PydanticValidationError as exc:
            attempts.append(
                ModelAttempt(
                    attempt_number=attempt_number, is_repair=is_repair, requested_model=model_name,
                    actual_model=model_name, status="schema_validation_failed", input_tokens=in_tok,
                    output_tokens=out_tok, estimated_cost_usd=est.estimated_cost_usd, actual_cost_usd=cost,
                    duration_ms=duration_ms, error_detail=str(exc)[:200],
                )
            )
            return None

        try:
            validate_explanation(result, prediction, set(allowed_source_ids))
        except ValidationError as exc:
            # Our own consistency check (validation.py) — the response was
            # valid JSON matching the schema, but the numbers/claims didn't
            # match the engine. Real usage still happened; still recorded.
            attempts.append(
                ModelAttempt(
                    attempt_number=attempt_number, is_repair=is_repair, requested_model=model_name,
                    actual_model=model_name, status=AI_STATUS_INCONSISTENT_WITH_ENGINE, input_tokens=in_tok,
                    output_tokens=out_tok, estimated_cost_usd=est.estimated_cost_usd, actual_cost_usd=cost,
                    duration_ms=duration_ms, error_detail=str(exc)[:200],
                )
            )
            return None

        attempts.append(
            ModelAttempt(
                attempt_number=attempt_number, is_repair=is_repair, requested_model=model_name, actual_model=model_name,
                status=AI_STATUS_SUCCESS, input_tokens=in_tok, output_tokens=out_tok,
                estimated_cost_usd=est.estimated_cost_usd, actual_cost_usd=cost, duration_ms=duration_ms,
                error_detail=None,
            )
        )
        return result

    overall_started = time.monotonic()

    allowed, block_status, est1 = _budget_check(requested_model)
    if not allowed:
        _record_blocked_attempt(1, False, requested_model, block_status, est1)
        return _fallback(
            f"Geschätzte Kosten über dem Limit (Modell: {requested_model})" if block_status == AI_STATUS_BLOCKED_COST_LIMIT
            else "Tagesbudget für KI-Analysen erreicht",
            block_status,
        )

    explanation = _try_model(1, False, requested_model, nano_client, est1)

    if explanation is None:
        allowed, block_status, est2 = _budget_check(requested_model)
        if allowed:
            repair_note = attempts[-1].error_detail if attempts else None
            explanation = _try_model(2, True, requested_model, nano_client, est2, repair_note=repair_note)  # one repair attempt, same model
        else:
            _record_blocked_attempt(2, True, requested_model, block_status, est2)

    used_mini = False
    if explanation is None and settings.openai_escalation_enabled:
        # GPT-5 nano failed twice in a row (or the repair was budget-
        # blocked) — the one documented condition under which the pricier
        # fallback model may be tried, still subject to the same budget,
        # re-checked fresh (accounting for whatever nano already cost).
        # Disabled by default (OPENAI_ESCALATION_ENABLED=false) until a
        # separately controlled test explicitly enables it.
        allowed, block_status, est3 = _budget_check(settings.openai_fallback_model)
        if allowed:
            explanation = _try_model(3, False, settings.openai_fallback_model, mini_client, est3)
            used_mini = explanation is not None
        else:
            _record_blocked_attempt(3, False, settings.openai_fallback_model, block_status, est3)

    duration_ms = int((time.monotonic() - overall_started) * 1000)
    if explanation is None:
        # Distinguish "a repair attempt genuinely ran and still failed"
        # from "the immediate cause was the last recorded attempt's own
        # status" — both are legitimate, but a repair having been tried at
        # all is itself meaningful information worth surfacing.
        repair_happened = any(a.is_repair and a.actual_model is not None for a in attempts)
        final_status = AI_STATUS_REPAIR_FAILED if repair_happened else (attempts[-1].status if attempts else AI_STATUS_BLOCKED_COST_LIMIT)
        return _fallback("GPT-Ausgabe zweimal ungültig oder nicht mit der Prognose konsistent", final_status)

    model_used = settings.openai_fallback_model if used_mini else requested_model
    return _persist_and_wrap(
        storage, settings, model_used, market_id, key_hash, prediction, explanation,
        duration_ms=duration_ms, cached=False, used_fallback=False, fallback_reason=None,
        requested_model=requested_model, final_status=AI_STATUS_SUCCESS, attempts=attempts,
    )
