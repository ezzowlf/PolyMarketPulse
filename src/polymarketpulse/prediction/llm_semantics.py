"""Phase G — optional LLM-assist for the rule-based semantics in
`semantics.py`.

This module NEVER replaces the deterministic rule-based parser/classifier.
It is only ever consulted when the rule-based result is already
genuinely ambiguous (``MarketProposition.proposition_status == "AMBIGUOUS"``
or ``EvidenceRelation.label == "AMBIGUOUS"``) and it is only ever allowed to
return *structured semantic fields* — proposition subject/predicate/
event_type/direction or evidence-relation label/entailment/direction — it
is never asked for, and never allowed to return, a bare probability number.
The deterministic engine (bayesian.py / ensemble.py / etc.) always computes
the actual forecast; the LLM's job here is strictly "understand this text a
little better than a keyword list could", nothing more.

Hard safety properties, all enforced in code (not just by convention):

1. Gated by ``Settings.llm_semantics_enabled`` (default False, see
   config.py) AND the existing ``Settings.ai_ready`` gate (AI enabled +
   API key present) — both must be true before any call is attempted.
2. Never invoked when the rule-based result is already confident — the
   caller-facing functions below check this themselves, so even a caller
   that forgets to check first gets a safe no-op.
3. The LLM response is validated against a strict Pydantic schema
   (reusing the same "reject anything that doesn't match" pattern as
   ai/schemas.py + ai/client.py's Structured Outputs strict mode). Any
   validation failure, any exception, any disabled/no-key state -> fall
   back to the original rule-based (possibly still-ambiguous) result.
   Nothing here ever raises out to the caller.
4. Reuses the existing `ai.client.OpenAIStructuredClient` wrapper — no
   second parallel LLM client is built.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..ai.client import AIError, OpenAIStructuredClient
from ..config import Settings
from .semantics import (
    EntailmentTag,
    EvidenceRelation,
    EvidenceRelationLabel,
    ExtractedEvent,
    MarketProposition,
)

logger = logging.getLogger(__name__)

LLM_SEMANTICS_PROMPT_VERSION = "v1"

_PROPOSITION_SYSTEM_PROMPT = (
    "Du hilfst dabei, die Aussage einer Prediction-Market-Frage strukturiert zu erfassen. "
    "Du gibst NIEMALS eine Wahrscheinlichkeit oder Prognose zurück — nur strukturierte "
    "semantische Felder (Subjekt, Prädikat/Event-Typ, Richtung, Deadline). Wenn du dir nicht "
    "sicher bist, setze die Felder auf null/unbekannt statt zu raten."
)

_EVIDENCE_SYSTEM_PROMPT = (
    "Du beurteilst, ob ein Nachrichtenereignis die YES-Bedingung einer Prediction-Market-Frage "
    "stützt, ihr widerspricht oder irrelevant ist. Du gibst NIEMALS eine Wahrscheinlichkeit oder "
    "Prognosezahl zurück — nur ein strukturiertes Urteil (Label, Entailment, Richtung, "
    "Direktheit). Wenn unklar, wähle AMBIGUOUS statt zu raten."
)


class LLMPropositionAssist(BaseModel):
    """Structured, schema-validated LLM output for proposition parsing.
    Deliberately narrow: only the fields the rule-based parser itself
    already produces, no probability field exists on this schema at all."""

    subject: str | None = None
    event_type: str | None = None
    direction: Literal["yes_if_occurs", "no_if_occurs", "unknown"] = "unknown"
    resolved_ambiguity: bool = Field(
        description="True only if the LLM is confident it resolved the ambiguity."
    )
    rationale: str

    model_config = {"extra": "forbid"}


class LLMEvidenceAssist(BaseModel):
    """Structured, schema-validated LLM output for evidence-relation
    classification. No probability/quantitative_weight field is exposed to
    the model — the deterministic weight table in semantics.py still
    assigns the numeric weight for the returned label."""

    label: EvidenceRelationLabel
    entailment: EntailmentTag
    resolved_ambiguity: bool = Field(
        description="True only if the LLM is confident it resolved the ambiguity."
    )
    rationale: str

    model_config = {"extra": "forbid"}


# Deterministic label -> (entailment, quantitative_weight) table, mirroring
# semantics.py's own tiering — the LLM only ever chooses a *label*, this
# module (not the LLM) is what turns that label into a numeric weight, so
# there is exactly one place in the codebase that maps labels to numbers.
_LABEL_WEIGHTS: dict[EvidenceRelationLabel, float] = {
    "DIRECT_YES": 1.0, "DIRECT_NO": 1.0,
    "SUPPORTS_YES": 0.55, "SUPPORTS_NO": 0.55,
    "WEAK_YES": 0.15, "WEAK_NO": 0.15,
    "CONTEXT": 0.0, "IRRELEVANT": 0.0, "AMBIGUOUS": 0.0,
}


def _build_client(settings: Settings) -> OpenAIStructuredClient | None:
    if not settings.llm_semantics_enabled:
        return None
    if not settings.ai_ready:
        return None
    return OpenAIStructuredClient(
        api_key=settings.openai_api_key,  # type: ignore[arg-type]
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
        max_output_tokens=settings.openai_max_output_tokens,
        reasoning_effort=settings.openai_reasoning_effort,
    )


def _proposition_prompt(question: str, resolution_text: str | None, rule_based_result: MarketProposition) -> str:
    return (
        "Marktfrage: " + question + "\n"
        "Resolution-Text: " + (resolution_text or "(keiner angegeben)") + "\n"
        "Regelbasierter Parser konnte nicht eindeutig entscheiden. Bisherige (unsichere) Felder: "
        f"subject={rule_based_result.subject!r}, event_type={rule_based_result.event_type!r}, "
        f"direction={rule_based_result.direction!r}, ambiguity_flags={list(rule_based_result.ambiguity_flags)!r}.\n"
        "Gib die tatsächliche Subjekt-Entität, den Event-Typ und die Richtung (yes_if_occurs / "
        "no_if_occurs / unknown) zurück, falls du sie eindeutig aus dem Text ableiten kannst."
    )


def llm_assist_proposition_parse(
    question: str,
    resolution_text: str | None,
    rule_based_result: MarketProposition,
    settings: Settings,
    client: OpenAIStructuredClient | None = None,
) -> MarketProposition | None:
    """Returns an improved `MarketProposition` (a copy of `rule_based_result`
    with subject/event_type/direction/proposition_status/ambiguity_flags
    updated) when the LLM confidently resolves a genuinely ambiguous
    rule-based parse, or `None` if the feature is off, the rule-based
    result was already confident (never called in that case), the call
    fails for any reason, or the response doesn't validate/doesn't resolve
    the ambiguity. Never raises."""
    if rule_based_result.proposition_status != "AMBIGUOUS":
        # Never override a confident rule-based result — do not even build
        # a client / consider calling out.
        return None

    active_client = client if client is not None else _build_client(settings)
    if active_client is None:
        return None

    try:
        parsed, _in_tok, _out_tok = active_client.generate_structured(
            _PROPOSITION_SYSTEM_PROMPT,
            _proposition_prompt(question, resolution_text, rule_based_result),
            LLMPropositionAssist,
            "market_proposition_assist",
        )
        assist = LLMPropositionAssist.model_validate(parsed)
    except AIError:
        logger.warning("llm_assist_proposition_parse: LLM call failed, falling back to rule-based result")
        return None
    except Exception:  # noqa: BLE001 - LLM-assist must never crash the caller; always fall back
        logger.warning("llm_assist_proposition_parse: unexpected failure, falling back to rule-based result")
        return None

    if not assist.resolved_ambiguity:
        return None
    if assist.subject is None and assist.event_type is None:
        return None

    new_flags = tuple(f for f in rule_based_result.ambiguity_flags if f not in ("no_subject_detected", "no_event_type_detected"))
    new_status: Literal["CLEAR", "AMBIGUOUS"] = "CLEAR" if (assist.subject or rule_based_result.subject) and (assist.event_type or rule_based_result.event_type) else "AMBIGUOUS"

    return rule_based_result.__class__(
        subject=assist.subject or rule_based_result.subject,
        predicate=assist.event_type or rule_based_result.predicate,
        object=rule_based_result.object,
        event_type=assist.event_type or rule_based_result.event_type,
        direction=assist.direction if assist.direction != "unknown" else rule_based_result.direction,
        threshold=rule_based_result.threshold,
        unit=rule_based_result.unit,
        location=rule_based_result.location,
        start_time=rule_based_result.start_time,
        deadline=rule_based_result.deadline,
        yes_condition=rule_based_result.yes_condition,
        no_condition=rule_based_result.no_condition,
        resolution_authority=rule_based_result.resolution_authority,
        ambiguity_flags=new_flags,
        proposition_status=new_status,
        asset=rule_based_result.asset,
        deadline_semantics=rule_based_result.deadline_semantics,
    )


def _evidence_prompt(proposition: MarketProposition, event: ExtractedEvent, rule_based_result: EvidenceRelation) -> str:
    return (
        f"Markt-Proposition: subject={proposition.subject!r}, event_type={proposition.event_type!r}, "
        f"direction={proposition.direction!r}, yes_condition={proposition.yes_condition!r}.\n"
        f"Extrahiertes Ereignis: actors={list(event.actors)!r}, action={event.action!r}, "
        f"event_type={event.event_type!r}, status={event.status!r}, certainty={event.certainty!r}.\n"
        f"Regelbasierte Einordnung war AMBIGUOUS ({rule_based_result.detail}).\n"
        "Wähle ein Label aus: DIRECT_YES, SUPPORTS_YES, WEAK_YES, DIRECT_NO, SUPPORTS_NO, WEAK_NO, "
        "CONTEXT, IRRELEVANT, AMBIGUOUS — und ein Entailment aus: ENTAILS, CONTRADICTS, NEUTRAL. "
        "Bleibe bei AMBIGUOUS, falls weiterhin unklar."
    )


def llm_assist_evidence_relation(
    proposition: MarketProposition,
    event: ExtractedEvent,
    rule_based_result: EvidenceRelation,
    settings: Settings,
    client: OpenAIStructuredClient | None = None,
) -> EvidenceRelation | None:
    """Returns an improved `EvidenceRelation` when the LLM confidently
    resolves a genuinely ambiguous rule-based classification, or `None`
    under every other circumstance (feature off, rule-based result already
    confident — never called then, call failure, invalid/unresolved
    response). Never raises."""
    if rule_based_result.label != "AMBIGUOUS":
        return None

    active_client = client if client is not None else _build_client(settings)
    if active_client is None:
        return None

    try:
        parsed, _in_tok, _out_tok = active_client.generate_structured(
            _EVIDENCE_SYSTEM_PROMPT,
            _evidence_prompt(proposition, event, rule_based_result),
            LLMEvidenceAssist,
            "evidence_relation_assist",
        )
        assist = LLMEvidenceAssist.model_validate(parsed)
    except AIError:
        logger.warning("llm_assist_evidence_relation: LLM call failed, falling back to rule-based result")
        return None
    except Exception:  # noqa: BLE001 - LLM-assist must never crash the caller; always fall back
        logger.warning("llm_assist_evidence_relation: unexpected failure, falling back to rule-based result")
        return None

    if not assist.resolved_ambiguity or assist.label == "AMBIGUOUS":
        return None

    return EvidenceRelation(
        label=assist.label,
        entailment=assist.entailment,
        quantitative_weight=_LABEL_WEIGHTS[assist.label],
        detail=f"LLM-Assist (v{LLM_SEMANTICS_PROMPT_VERSION}): {assist.rationale}",
    )
