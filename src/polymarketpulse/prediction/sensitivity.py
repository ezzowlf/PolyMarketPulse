"""Phase K — forecast sensitivity and honest counterfactual audit.

Only the linear pre-news ensemble can be recomputed exactly from an existing
PredictionResult: it is the same ``combine_submodels`` calculation used by
the engine.  News is deliberately marked NOT_APPLICABLE because it enters
through a separate Bayesian update and its raw article inputs are not held on
PredictionResult.  Market price is likewise never a model input, so removing
it has no model counterfactual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .ensemble import combine_submodels
from .types import SubmodelEstimate

CounterfactualStatus = Literal["COMPUTED", "NOT_APPLICABLE", "UNAVAILABLE"]


@dataclass(frozen=True)
class Counterfactual:
    removed_input: str
    status: CounterfactualStatus
    baseline_probability: float | None
    without_probability: float | None
    delta: float | None
    detail: str

    def as_dict(self) -> dict:
        return {
            "removed_input": self.removed_input,
            "status": self.status,
            "baseline_probability": self.baseline_probability,
            "without_probability": self.without_probability,
            "delta": self.delta,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SensitivityAudit:
    applies_to: str
    baseline_probability: float | None
    counterfactuals: tuple[Counterfactual, ...] = field(default_factory=tuple)
    strongest_input: str | None = None
    strongest_delta: float | None = None
    fragility: Literal["SINGLE_INPUT", "MEASURED", "UNKNOWN"] = "UNKNOWN"

    def as_dict(self) -> dict:
        return {
            "applies_to": self.applies_to,
            "baseline_probability": self.baseline_probability,
            "counterfactuals": [item.as_dict() for item in self.counterfactuals],
            "strongest_input": self.strongest_input,
            "strongest_delta": self.strongest_delta,
            "fragility": self.fragility,
        }


def derive_sensitivity_audit(submodels: tuple[SubmodelEstimate, ...]) -> SensitivityAudit:
    """Remove each actual linear ensemble input and recompute it exactly.

    This never claims to recalculate the final posterior: `news` is a
    separate non-linear Bayesian input.  The returned baseline is therefore
    explicitly the pre-news linear ensemble, which makes every reported
    delta inspectable and reproducible.
    """
    linear = tuple(
        model for model in submodels
        if model.name != "news" and model.available and model.estimated_yes_probability is not None and model.weight > 0
    )
    baseline, _ = combine_submodels(list(linear))
    counterfactuals: list[Counterfactual] = []
    if baseline is not None:
        for model in linear:
            without, _ = combine_submodels([other for other in linear if other.name != model.name])
            counterfactuals.append(
                Counterfactual(
                    removed_input=model.name,
                    status="COMPUTED" if without is not None else "UNAVAILABLE",
                    baseline_probability=baseline,
                    without_probability=without,
                    delta=round(without - baseline, 4) if without is not None else None,
                    detail=(
                        "Exact removal/recompute of the pre-news linear ensemble."
                        if without is not None else "No remaining weighted linear input after removal."
                    ),
                )
            )
    counterfactuals.extend((
        Counterfactual(
            removed_input="news",
            status="NOT_APPLICABLE",
            baseline_probability=None,
            without_probability=None,
            delta=None,
            detail="News enters the final posterior through Bayesian evidence, not the linear ensemble.",
        ),
        Counterfactual(
            removed_input="market_price",
            status="NOT_APPLICABLE",
            baseline_probability=None,
            without_probability=None,
            delta=None,
            detail="Market price is never an input to the independent forecast ensemble.",
        ),
    ))
    computed = [item for item in counterfactuals if item.status == "COMPUTED" and item.delta is not None]
    strongest = max(computed, key=lambda item: abs(item.delta), default=None)
    return SensitivityAudit(
        applies_to="pre_news_linear_ensemble",
        baseline_probability=baseline,
        counterfactuals=tuple(counterfactuals),
        strongest_input=strongest.removed_input if strongest else None,
        strongest_delta=strongest.delta if strongest else None,
        fragility="SINGLE_INPUT" if len(linear) == 1 else "MEASURED" if baseline is not None else "UNKNOWN",
    )
