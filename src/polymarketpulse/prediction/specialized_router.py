"""Specialized Model Router — route propositions to specialized forecasting
models based on event_type and category.

Routing logic:
  - price_above / price_below → Quant
  - central_bank_decision / rate_cut / rate_hike / rate_hold → Macro
  - office_departure / legislation / election / appointment / court_outcome → Politics
  - ceasefire / war_escalation / military_action / sanctions / territorial_control / strategic_waterway / diplomatic_agreement → Geopolitics
  - sport_match / sport_tournament / sport_qualification / sport_winner / sport_final → Sports
  - Unknown event type → do not force it into a model

Returns structured routing result with:
  - eligible_models: all models that could handle this proposition
  - used_models: models that were actually selected
  - unavailable_models: models that were eligible but unavailable
  - reasons: why each model was selected or not"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from .geopolitics import GeopoliticsResult, analyze_geopolitics
from .macro import MacroResult, analyze_macro
from .politics import PoliticsResult, analyze_politics
from .quant import QuantResult, analyze_quant
from .semantics import MarketProposition
from .sports import SportsResult, analyze_sports

# Mapping from event_type to model name and handler
_EVENT_TYPE_TO_MODEL: dict[str, str] = {
    # Quantitative price-threshold
    "price_above": "quant",
    "price_below": "quant",
    # Macro / Central Bank
    "central_bank_decision": "macro",
    "rate_cut": "macro",
    "rate_hike": "macro",
    "rate_hold": "macro",
    "monetary_policy": "macro",
    "policy_change": "macro",
    # Politics
    "office_departure": "politics",
    "office_status": "politics",
    "resignation": "politics",
    "removal": "politics",
    "impeachment": "politics",
    "election": "politics",
    "legislation": "politics",
    "appointment": "politics",
    "court_outcome": "politics",
    # Geopolitics
    "ceasefire": "geopolitics",
    "war_escalation": "geopolitics",
    "military_action": "geopolitics",
    "sanctions": "geopolitics",
    "territorial_control": "geopolitics",
    "strategic_waterway": "geopolitics",
    "diplomatic_agreement": "geopolitics",
    # Sports
    "sport_match": "sports",
    "sport_tournament": "sports",
    "sport_qualification": "sports",
    "sport_winner": "sports",
    "sport_final": "sports",
}

# Reverse mapping: model name → event types it handles
_MODEL_TO_EVENT_TYPES: dict[str, frozenset[str]] = {
    "quant": frozenset({"price_above", "price_below"}),
    "macro": frozenset({
        "central_bank_decision", "rate_cut", "rate_hike", "rate_hold",
        "monetary_policy", "policy_change",
    }),
    "politics": frozenset({
        "office_departure", "office_status", "resignation", "removal",
        "impeachment", "election", "legislation", "appointment", "court_outcome",
    }),
    "geopolitics": frozenset({
        "ceasefire", "war_escalation", "military_action", "sanctions",
        "territorial_control", "strategic_waterway", "diplomatic_agreement",
    }),
    "sports": frozenset({
        "sport_match", "sport_tournament", "sport_qualification",
        "sport_winner", "sport_final",
    }),
}

# Category-based overrides (when event_type alone is ambiguous)
_CATEGORY_TO_PREFERRED_MODEL: dict[str, str] = {
    "CENTRAL_BANKS": "macro",
    "POLITICS": "politics",
    "WAR_PEACE": "geopolitics",
    "SPORT_OTHER": "sports",
}


@dataclass(frozen=True)
class ModelRoutingResult:
    """Result of the routing decision."""

    proposition_text: str
    event_type: str | None
    category: str | None
    selected_model: str | None
    eligible_models: tuple[str, ...]
    used_models: tuple[str, ...]
    unavailable_models: tuple[str, ...]
    reasons: tuple[str, ...]
    model_results: tuple[dict, ...]  # Results from used models
    source_coverage: dict[str, bool]  # E8: source coverage

    def as_dict(self) -> dict:
        return {
            "proposition_text": self.proposition_text,
            "event_type": self.event_type,
            "category": self.category,
            "selected_model": self.selected_model,
            "eligible_models": list(self.eligible_models),
            "used_models": list(self.used_models),
            "unavailable_models": list(self.unavailable_models),
            "reasons": list(self.reasons),
            "model_results": list(self.model_results),
            "source_coverage": self.source_coverage,
        }


def _get_eligible_models(event_type: str | None, category: str | None) -> list[str]:
    """Determine which models could potentially handle this proposition."""
    eligible: set[str] = set()

    if event_type is not None:
        model = _EVENT_TYPE_TO_MODEL.get(event_type)
        if model:
            eligible.add(model)

    if category is not None:
        preferred = _CATEGORY_TO_PREFERRED_MODEL.get(category)
        if preferred:
            eligible.add(preferred)

    return sorted(eligible)


def _filter_kwargs_for(func, kwargs: dict) -> dict:
    """Only forward the kwargs that `func`'s own signature accepts.

    Every analyze_* model has a different, deliberately narrow parameter
    list (see each module). Previously this router forwarded **kwargs
    blindly to all of them (plus re-passing subject/location explicitly
    for politics), which raised TypeError ("unexpected keyword argument" /
    "got multiple values") for every real proposition that carried a
    subject/location — i.e. the router crashed on exactly the markets it
    was supposed to route. Filtering by signature fixes that generically
    instead of hardcoding a per-model allowlist that would drift again."""
    accepted = set(inspect.signature(func).parameters)
    return {k: v for k, v in kwargs.items() if k in accepted}


def _run_model_if_available(
    model_name: str,
    text: str,
    event_type: str | None,
    proposition_status: str,
    **kwargs,
) -> tuple[bool, dict | None, str]:
    """Run a model if it's available, returning (available, result, reason)."""
    if model_name == "quant":
        result: QuantResult | None = None
        try:
            result = analyze_quant(
                text=text,
                event_type=event_type,
                proposition_status=proposition_status,
                **_filter_kwargs_for(analyze_quant, kwargs),
            )
        except Exception as e:  # noqa: BLE001 - model call is user/data-driven; must never crash the router
            return False, None, f"Quant model error: {e}"

        if result and result.available and result.probability is not None:
            return True, result.as_dict(), f"Quant model: {result.reason}"
        return False, None, result.reason if result else "Quant model unavailable"

    elif model_name == "macro":
        result: MacroResult | None = None
        try:
            result = analyze_macro(
                text=text,
                event_type=event_type,
                proposition_status=proposition_status,
                **_filter_kwargs_for(analyze_macro, kwargs),
            )
        except Exception as e:  # noqa: BLE001 - model call is user/data-driven; must never crash the router
            return False, None, f"Macro model error: {e}"

        if result and result.available and result.probability is not None:
            return True, result.as_dict(), f"Macro model: {result.reason}"
        return False, None, result.reason if result else "Macro model unavailable"

    elif model_name == "politics":
        result: PoliticsResult | None = None
        try:
            result = analyze_politics(
                text=text,
                event_type=event_type,
                proposition_status=proposition_status,
                **_filter_kwargs_for(analyze_politics, kwargs),
            )
        except Exception as e:  # noqa: BLE001 - model call is user/data-driven; must never crash the router
            return False, None, f"Politics model error: {e}"

        if result and result.available and result.probability is not None:
            return True, result.as_dict(), f"Politics model: {result.reason}"
        # Check for Trump/Nevada protection
        if result and result.reason == "Trump/Nevada office departure case — protected regression case":
            return False, None, result.reason
        return False, None, result.reason if result else "Politics model unavailable"

    elif model_name == "geopolitics":
        result: GeopoliticsResult | None = None
        try:
            result = analyze_geopolitics(
                text=text,
                event_type=event_type,
                proposition_status=proposition_status,
                **_filter_kwargs_for(analyze_geopolitics, kwargs),
            )
        except Exception as e:  # noqa: BLE001 - model call is user/data-driven; must never crash the router
            return False, None, f"Geopolitics model error: {e}"

        if result and result.available and result.probability is not None:
            return True, result.as_dict(), f"Geopolitics model: {result.reason}"
        return False, None, result.reason if result else "Geopolitics model unavailable"

    elif model_name == "sports":
        result: SportsResult | None = None
        try:
            result = analyze_sports(
                text=text,
                event_type=event_type,
                proposition_status=proposition_status,
                **_filter_kwargs_for(analyze_sports, kwargs),
            )
        except Exception as e:  # noqa: BLE001 - model call is user/data-driven; must never crash the router
            return False, None, f"Sports model error: {e}"

        if result and result.available and result.probability is not None:
            return True, result.as_dict(), f"Sports model: {result.reason}"
        return False, None, result.reason if result else "Sports model unavailable"

    return False, None, f"Unknown model: {model_name}"


def route_to_specialized_model(
    proposition: MarketProposition,
    text: str,
    current_price: float | None = None,
    historical_volatility: float | None = None,
) -> ModelRoutingResult:
    """Main entry point: route proposition to appropriate specialized model(s).

    Args:
        proposition: Parsed MarketProposition from semantics.py
        text: The proposition text to analyze
        current_price: Current underlying price (for quant models)
        historical_volatility: Historical volatility (for quant models)

    Returns:
        ModelRoutingResult with routing decision and model outputs."""
    event_type = proposition.event_type
    category = None  # Can be added later if needed
    proposition_status = proposition.proposition_status

    # Get eligible models
    eligible_models = _get_eligible_models(event_type, category)

    if not eligible_models:
        return ModelRoutingResult(
            proposition_text=text,
            event_type=event_type,
            category=category,
            selected_model=None,
            eligible_models=(),
            used_models=(),
            unavailable_models=(),
            reasons=("No specialized model available for this event_type",),
            model_results=(),
            source_coverage={
                "history_available": True,
                "evidence_available": True,
                "politics_available": False,
                "geopolitics_available": False,
                "macro_available": False,
                "quant_available": False,
                "sports_available": False,
                "event_relations_available": True,
            },
        )

    # Select primary model
    selected_model = eligible_models[0]
    reasons: list[str] = [f"Selected model: {selected_model} based on event_type '{event_type}'"]

    used_models: list[str] = []
    unavailable_models: list[str] = []
    model_results: list[dict] = []

    # Run the selected model
    available, result, reason = _run_model_if_available(
        selected_model,
        text,
        event_type,
        proposition_status,
        threshold=proposition.threshold,
        asset=proposition.asset,
        current_price=current_price,
        historical_volatility=historical_volatility,
        subject=proposition.subject,
        location=proposition.location,
        deadline=proposition.deadline,
    )

    if available and result:
        used_models.append(selected_model)
        model_results.append(result)
        reasons.append(reason)
    else:
        unavailable_models.append(selected_model)
        reasons.append(f"{selected_model}: {reason}")

    # Build source coverage (E8)
    source_coverage: dict[str, bool] = {
        "history_available": True,  # Always available (historical baseline)
        "evidence_available": True,  # Always available (independent evidence)
        "politics_available": "politics" in eligible_models,
        "geopolitics_available": "geopolitics" in eligible_models,
        "macro_available": "macro" in eligible_models,
        "quant_available": "quant" in eligible_models,
        "sports_available": "sports" in eligible_models,
        "event_relations_available": True,  # Always available (event_relations.py)
    }

    return ModelRoutingResult(
        proposition_text=text,
        event_type=event_type,
        category=category,
        selected_model=selected_model if used_models else None,
        eligible_models=tuple(eligible_models),
        used_models=tuple(used_models),
        unavailable_models=tuple(unavailable_models),
        reasons=tuple(reasons),
        model_results=tuple(model_results),
        source_coverage=source_coverage,
    )


# Convenience functions for common cases
def route_price_threshold(
    proposition: MarketProposition,
    text: str,
    current_price: float | None = None,
    historical_volatility: float | None = None,
) -> ModelRoutingResult:
    """Route price_above/price_below markets to Quant model."""
    return route_to_specialized_model(
        proposition, text, current_price, historical_volatility
    )


def route_politics(
    proposition: MarketProposition,
    text: str,
) -> ModelRoutingResult:
    """Route politics markets to Politics model."""
    return route_to_specialized_model(proposition, text)


def route_geopolitics(
    proposition: MarketProposition,
    text: str,
) -> ModelRoutingResult:
    """Route geopolitics markets to Geopolitics model."""
    return route_to_specialized_model(proposition, text)


def route_macro(
    proposition: MarketProposition,
    text: str,
) -> ModelRoutingResult:
    """Route macro markets to Macro model."""
    return route_to_specialized_model(proposition, text)


def route_sports(
    proposition: MarketProposition,
    text: str,
) -> ModelRoutingResult:
    """Route sports markets to Sports model."""
    return route_to_specialized_model(proposition, text)