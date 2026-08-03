from __future__ import annotations

import time
from datetime import UTC, datetime

from ..config import Settings
from ..storage import Storage
from .cache import hash_payload, lookup
from .client import AIContextError, AIDisabledError, AIResponseError, OpenAIStructuredClient
from .context_builder import build_market_context, context_hash
from .prompts import (
    SYSTEM_PROMPT,
    build_ask_prompt,
    build_compare_prompt,
    build_explain_market_prompt,
    build_explain_signal_prompt,
    build_news_analysis_prompt,
)
from .schemas import DISCLAIMER, PROMPT_VERSION, AIAnalysisResponse, AIRunMeta, AnalysisResult


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
