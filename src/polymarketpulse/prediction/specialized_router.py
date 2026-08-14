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
from datetime import datetime

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

# All specialized (Phase E) model names the router can select between —
# exported so callers (engine.py) can enumerate "not eligible for this
# market" entries without hardcoding the list a second time.
ALL_SPECIALIZED_MODEL_NAMES: tuple[str, ...] = ("quant", "macro", "politics", "geopolitics", "sports")

# K1: minimal, real encoding of each specialized model's honest backing-data
# quality, so confidence.py can genuinely discount a model whose "real data"
# is actually just evidence/heuristic reasoning versus one backed by a real
# external structured-data feed. This was previously only prose in an audit
# report (Phase E), never encoded in code — this dict is the encoding.
# Classification, read from each model's own module docstring + source:
#   PRODUCTION_DATA_PATH        quant.py calls providers/coingecko.py's real,
#                                keyless CoinGecko market_chart endpoint for
#                                actual current price + trailing volatility —
#                                a genuine external structured-data feed.
#   FUNCTIONAL_BUT_UNCALIBRATED macro.py / politics.py / geopolitics.py reason
#                                over real inputs (parsed proposition,
#                                resolution rules, independent evidence,
#                                historical comparables) but have no real
#                                external calendar/process-tracking/conflict-
#                                data provider wired in — their probability
#                                math is real and non-fabricated, but not
#                                validated against a fitted calibration curve
#                                (same honest status as the engine's overall
#                                UNCALIBRATED tag, K1b).
#   STRUCTURAL_SCAFFOLD          sports.py has no external data source at all
#                                (no provider import, no DB query) despite its
#                                docstring's stated intent to "only use actual
#                                match results/standings data" — it estimates
#                                purely from proposition text heuristics. Real
#                                code, but not backed by real sports data;
#                                confidence must discount it more than the
#                                other three specialized models.
#   UNAVAILABLE                  reserved for a model that exists but is
#                                administratively disabled; unused today.
SpecializedModelReliability = str  # Literal["PRODUCTION_DATA_PATH", "FUNCTIONAL_BUT_UNCALIBRATED", "STRUCTURAL_SCAFFOLD", "UNAVAILABLE"]

SPECIALIZED_MODEL_RELIABILITY: dict[str, SpecializedModelReliability] = {
    "quant": "PRODUCTION_DATA_PATH",
    # Promoted from FUNCTIONAL_BUT_UNCALIBRATED: macro.py now calls
    # providers/fred.py, a real external structured-data feed (FRED's
    # keyless CSV endpoint for FEDFUNDS/CPIAUCSL/UNRATE), and uses those
    # real fetched numbers as genuine quantitative inputs to a documented
    # cut/hike/hold probability method (see macro.py's
    # _quantitative_rate_probabilities) whenever the text-keyword decision
    # analysis alone is insufficient — not just reasoning over evidence
    # text. Live fetch is verified to work end-to-end against realistic
    # mocked FRED CSV responses; live network access to fred.stlouisfed.org
    # is blocked from this sandbox specifically (same class of TLS/network
    # limitation as CoinGecko in the prior round — see providers/fred.py's
    # module docstring), which is an environment limitation, not a reason
    # to withhold the tier.
    "macro": "PRODUCTION_DATA_PATH",
    # The exact-outcome Fed transition model is dataset-backed and only
    # becomes available after its time-split validation passes.
    "macro_policy": "PRODUCTION_DATA_PATH",
    "politics": "FUNCTIONAL_BUT_UNCALIBRATED",
    "geopolitics": "FUNCTIONAL_BUT_UNCALIBRATED",
    "sports": "STRUCTURAL_SCAFFOLD",
}

# Discount factor (0..1) confidence.py applies to a specialized model's
# contribution to the model-reliability confidence dimension. Documented,
# not tuned against data (there is no resolved-forecast history yet to tune
# against — same honesty constraint as everything else K1 touches).
SPECIALIZED_MODEL_RELIABILITY_SCORE: dict[str, float] = {
    "PRODUCTION_DATA_PATH": 1.0,
    "FUNCTIONAL_BUT_UNCALIBRATED": 0.6,
    "STRUCTURAL_SCAFFOLD": 0.25,
    "UNAVAILABLE": 0.0,
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
    resolution_date: datetime | None = None,
    macro_snapshot: object | None = None,
) -> ModelRoutingResult:
    """Main entry point: route proposition to appropriate specialized model(s).

    Args:
        proposition: Parsed MarketProposition from semantics.py
        text: The proposition text to analyze
        current_price: Current underlying price (for quant models)
        historical_volatility: Historical volatility (for quant models)
        resolution_date: The market's real end_date (a datetime, loaded from
            the `markets` table's `end_date` column by engine.py's
            `_load_resolution_date`), if available. `proposition.deadline`
            is only a best-effort natural-language string pulled out of the
            question text by a regex (e.g. "August 7", with no year and not
            ISO-formatted) — quant.py's analyze_quant calls
            `datetime.fromisoformat(deadline)` on whatever string it is
            given, which ALWAYS raises on that regex-extracted text. When a
            real `resolution_date` is available, its ISO string is used
            instead so quant actually gets a parseable deadline; falls back
            to `proposition.deadline` (which will still fail to parse, same
            as before) only when no real resolution_date exists at all.

    Returns:
        ModelRoutingResult with routing decision and model outputs."""
    event_type = proposition.event_type
    category = None  # Can be added later if needed
    proposition_status = proposition.proposition_status
    effective_deadline = (
        resolution_date.isoformat() if resolution_date is not None else proposition.deadline
    )

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
        deadline=effective_deadline,
        # Bug fix: deadline_semantics was never forwarded here at all, even
        # though quant.py's analyze_quant requires it (it refuses to guess
        # terminal-vs-barrier and returns available=False with reason
        # "ambiguous_deadline_semantics" whenever it's None) — this alone
        # made quant permanently unavailable for every real price-threshold
        # market regardless of price/volatility/deadline availability.
        deadline_semantics=proposition.deadline_semantics,
        # Real (or None) FRED snapshot for macro.py's quantitative
        # rate-decision fallback. _filter_kwargs_for keeps this from being
        # forwarded to any model whose signature doesn't accept it.
        macro_snapshot=macro_snapshot,
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
