from __future__ import annotations

import time
from datetime import UTC, datetime

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
from .cost import estimate_cost, estimate_tokens_from_text, within_daily_budget
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


def _build_recommendation_payload(market: dict, prediction: PredictionResult, allowed_source_ids: list[str]) -> dict:
    """Compact, bounded input for GPT-5 nano — mirrors the example structure
    exactly. No raw tables, no unfiltered dumps."""
    positive = [
        {"factor": note, "weight": None, "source_ids": [f"reasoning_{i}"]}
        for i, note in enumerate(prediction.reasoning_notes)
        if prediction.net_yes_edge is not None and prediction.net_yes_edge >= 0
    ]
    negative = [
        {"factor": note, "weight": None, "source_ids": [f"reasoning_{i}"]}
        for i, note in enumerate(prediction.reasoning_notes)
        if prediction.net_yes_edge is not None and prediction.net_yes_edge < 0
    ]
    return {
        "task": "Explain an already calculated prediction market analysis.",
        "language": "de",
        "market": {
            "id": market["market_id"],
            "question": market["question"],
            "category": market["category"],
            "resolution_date": market["end_date"],
            "resolution_rules": market["resolution_source"] or "nicht angegeben",
        },
        "prediction": {
            "market_yes_probability": prediction.market_yes_probability,
            "estimated_yes_probability": prediction.estimated_yes_probability,
            "estimated_no_probability": prediction.estimated_no_probability,
            "net_yes_edge": prediction.net_yes_edge,
            "confidence_score": prediction.confidence_score,
            "data_quality_score": prediction.data_quality.total,
            "uncertainty_interval": {
                "lower": prediction.uncertainty_lower,
                "upper": prediction.uncertainty_upper,
            },
            "recommendation": prediction.recommendation,
        },
        "positive_factors": positive,
        "negative_factors": negative,
        "historical_comparisons": [
            {
                "description": f"{prediction.comparable_sample_size} vergleichbare Begegnungen",
                "observed_yes_rate": prediction.observed_historical_yes_rate,
                "sample_size": prediction.comparable_sample_size,
                "source_ids": ["historical_comparables"] if prediction.comparable_sample_size else [],
            }
        ],
        "data_gaps": [] if prediction.comparable_sample_size >= 5 else ["Zu wenige historische Vergleichsfälle"],
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
    input_tokens: int | None,
    output_tokens: int | None,
    cached_input_tokens: int | None,
    estimated_cost_usd: float | None,
    actual_cost_usd: float | None,
    cached: bool,
    used_fallback: bool,
    fallback_reason: str | None,
) -> ExplainRecommendationResponse:
    run_id = storage.record_ai_run(
        _ANALYSIS_TYPE_EXPLAIN_RECOMMENDATION, market_id, model, EXPLANATION_PROMPT_VERSION, key_hash,
        status="completed", duration_ms=duration_ms, input_tokens=input_tokens, output_tokens=output_tokens,
        cached=cached, error_code=None, response_json=explanation.model_dump_json(),
        cached_input_tokens=cached_input_tokens, estimated_cost_usd=estimated_cost_usd,
        actual_cost_usd=actual_cost_usd,
    )
    return ExplainRecommendationResponse(
        prediction=prediction.as_dict(),
        explanation=explanation,
        meta=ExplanationRunMeta(
            analysis_id=run_id,
            model=model,
            prompt_version=EXPLANATION_PROMPT_VERSION,
            cached=cached,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            actual_cost_usd=actual_cost_usd,
            used_fallback=used_fallback,
            fallback_reason=fallback_reason,
            created_at=datetime.now(UTC).isoformat(),
        ),
    )


def _persist_prediction_snapshot(storage: Storage, market: dict, prediction: PredictionResult) -> None:
    """Every computed prediction is stored for later evaluation
    (evaluation.py), independent of whether GPT was ever called — this is
    what lets accuracy/precision/recall/Brier/log-loss/calibration/edge/ROI
    be measured against the engine alone, not just AI-explained runs."""
    storage.save_prediction_snapshot(
        market_id=prediction.market_id, provider=market["provider"],
        provider_market_id=market["provider_market_id"], category=market["category"],
        prediction_version=PREDICTION_VERSION, market_yes_probability=prediction.market_yes_probability,
        estimated_yes_probability=prediction.estimated_yes_probability, net_yes_edge=prediction.net_yes_edge,
        confidence_score=prediction.confidence_score, recommendation=prediction.recommendation,
        comparable_sample_size=prediction.comparable_sample_size,
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
        # The fallback path always persists input_tokens=None (see _fallback()
        # below); a genuine model response always has a real token count.
        # Without this check, a cached *fallback* explanation would be
        # reported as a real KI response on every subsequent cache hit.
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

    def _fallback(reason: str) -> ExplainRecommendationResponse:
        explanation = build_fallback_explanation(prediction)
        return _persist_and_wrap(
            storage, settings, settings.openai_model, market_id, key_hash, prediction, explanation,
            duration_ms=0, input_tokens=None, output_tokens=None, cached_input_tokens=None,
            estimated_cost_usd=None, actual_cost_usd=0.0, cached=False, used_fallback=True, fallback_reason=reason,
        )

    if not settings.ai_ready:
        return _fallback("AI deaktiviert oder kein API-Key konfiguriert")

    payload = _build_recommendation_payload(market, prediction, allowed_source_ids)
    user_prompt = build_explain_recommendation_input(payload)

    est_input_tokens = estimate_tokens_from_text(EXPLANATION_SYSTEM_PROMPT + user_prompt)
    est_output_tokens = settings.openai_max_output_tokens
    if est_input_tokens > settings.openai_max_input_tokens:
        return _fallback("Eingabe überschreitet Token-Limit selbst nach Verdichtung")

    estimate = estimate_cost(settings.openai_model, est_input_tokens, est_output_tokens)
    if estimate.estimated_cost_usd > settings.openai_max_cost_per_analysis_usd:
        return _fallback(
            f"Geschätzte Kosten ({estimate.estimated_cost_usd:.5f} USD) über dem Limit "
            f"({settings.openai_max_cost_per_analysis_usd:.5f} USD)"
        )
    if not within_daily_budget(storage.connection, settings.openai_daily_budget_usd, estimate.estimated_cost_usd):
        return _fallback("Tagesbudget für KI-Analysen erreicht")

    def _try_model(model_name: str, client: OpenAIStructuredClient | None) -> tuple[ExplanationResult, int, int] | None:
        c = client or OpenAIStructuredClient(
            api_key=settings.openai_api_key,  # type: ignore[arg-type]
            model=model_name,
            timeout_seconds=settings.openai_timeout_seconds,
            max_output_tokens=settings.openai_max_output_tokens,
        )
        try:
            parsed, in_tok, out_tok = c.generate_structured(
                EXPLANATION_SYSTEM_PROMPT, user_prompt, ExplanationResult, "explanation"
            )
            result = ExplanationResult.model_validate(parsed)
            validate_explanation(result, prediction, set(allowed_source_ids))
            return result, in_tok or est_input_tokens, out_tok or 0
        except (AIError, ValidationError):
            # Covers AIResponseError (bad/invalid JSON), AITimeoutError,
            # AIRateLimitError, AINetworkError, and our own ValidationError
            # (number mismatch / invented source) — every one of these must
            # fall through to a retry or the rule-based fallback, never crash.
            return None

    started = time.monotonic()
    outcome = _try_model(settings.openai_model, nano_client)
    if outcome is None:
        outcome = _try_model(settings.openai_model, nano_client)  # one repair attempt, same model
    used_mini = False
    if outcome is None:
        # GPT-5 nano failed validation/parsing twice in a row — the one
        # documented condition under which the (pricier) fallback model may
        # be tried automatically, still subject to the same cost budget.
        mini_estimate = estimate_cost(settings.openai_fallback_model, est_input_tokens, est_output_tokens)
        if (
            mini_estimate.estimated_cost_usd <= settings.openai_max_cost_per_analysis_usd
            and within_daily_budget(storage.connection, settings.openai_daily_budget_usd, mini_estimate.estimated_cost_usd)
        ):
            outcome = _try_model(settings.openai_fallback_model, mini_client)
            used_mini = True

    duration_ms = int((time.monotonic() - started) * 1000)
    if outcome is None:
        return _fallback("GPT-Ausgabe zweimal ungültig oder nicht mit der Prognose konsistent")

    explanation, input_tokens, output_tokens = outcome
    model_used = settings.openai_fallback_model if used_mini else settings.openai_model
    actual = estimate_cost(model_used, input_tokens, output_tokens).estimated_cost_usd

    return _persist_and_wrap(
        storage, settings, model_used, market_id, key_hash, prediction, explanation,
        duration_ms=duration_ms, input_tokens=input_tokens, output_tokens=output_tokens,
        cached_input_tokens=0, estimated_cost_usd=estimate.estimated_cost_usd, actual_cost_usd=actual,
        cached=False, used_fallback=False, fallback_reason=None,
    )
