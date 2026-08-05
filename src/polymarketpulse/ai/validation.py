from __future__ import annotations

from ..prediction import PredictionResult
from .fallback import direction_for
from .schemas import ExplanationResult

# Rounding tolerance in percentage points — GPT is asked to echo rounded
# whole-percent numbers, so exact float equality would be too strict.
TOLERANCE_PP = 1.0


class ValidationError(Exception):
    """The model changed, invented, or omitted a number it was only
    supposed to explain. Never displayed to the user directly — always
    triggers a repair attempt or the rule-based fallback."""


def _pct(value: float | None) -> float | None:
    return round(value * 100) if value is not None else None


def validate_explanation(
    explanation: ExplanationResult, prediction: PredictionResult, allowed_source_ids: set[str]
) -> None:
    """Raises ValidationError on the first mismatch. Never silently
    'corrects' the model's output — a wrong number is always rejected, not
    patched, since we can't know which side is right."""
    expected_direction = direction_for(prediction.recommendation)
    if explanation.direction != expected_direction:
        raise ValidationError(
            f"direction mismatch: model said {explanation.direction!r}, engine implies {expected_direction!r}"
        )

    if explanation.recommendation != prediction.recommendation:
        raise ValidationError(
            f"recommendation mismatch: model said {explanation.recommendation!r}, "
            f"engine computed {prediction.recommendation!r}"
        )

    checks = (
        ("market_yes_percent", explanation.probability_explanation.market_yes_percent, _pct(prediction.market_yes_probability)),
        ("model_yes_percent", explanation.probability_explanation.model_yes_percent, _pct(prediction.estimated_yes_probability)),
        ("model_no_percent", explanation.probability_explanation.model_no_percent, _pct(prediction.estimated_no_probability)),
        ("net_edge_percentage_points", explanation.probability_explanation.net_edge_percentage_points, _pct(prediction.net_yes_edge)),
    )
    for name, actual, expected in checks:
        if expected is None:
            continue  # engine didn't have this value either; nothing to check
        if actual is None or abs(actual - expected) > TOLERANCE_PP:
            raise ValidationError(f"{name} mismatch: model said {actual!r}, engine computed {expected!r}")

    cited_ids = {
        sid
        for factor in (*explanation.supports_yes, *explanation.supports_no)
        for sid in factor.source_ids
    }
    unknown = cited_ids - allowed_source_ids
    if unknown:
        raise ValidationError(f"unknown/invented source_ids: {sorted(unknown)}")
