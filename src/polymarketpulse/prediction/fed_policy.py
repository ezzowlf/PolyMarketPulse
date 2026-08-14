"""Reproducible Fed exact-outcome transition model.

This is intentionally a small, interpretable empirical model rather than a
generic macro score.  Its dataset records every scheduled 2021--2025 FOMC
meeting and the policy action known on that meeting date.  The target action
rows are transcribed from the Federal Reserve's official open-market history;
scheduled hold rows are paired with the official FOMC calendars.  The dataset
does not contain macro vintages, so the only approved feature is the prior
policy action.  That limitation is explicit in validation and confidence.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..providers.fedboard import FedPolicyDecision
from ..providers.fred import MacroSnapshot

DATASET_ID = "fed_fomc_actions_2021_2025"
DATASET_VERSION = "2026.08.14.1"
MODEL_ID = "fed_prior_action_transition"
MODEL_VERSION = "2026.08.14.1"
FED_OPEN_MARKET_SOURCE = "https://www.federalreserve.gov/monetarypolicy/openmarket.htm"
FED_CALENDAR_SOURCE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
OUTCOMES = ("CUT_50_PLUS", "CUT_25", "UNCHANGED", "HIKE_25", "HIKE_50_PLUS")


@dataclass(frozen=True)
class FedMeeting:
    meeting_date: date
    action: str
    source_url: str = FED_OPEN_MARKET_SOURCE


@dataclass(frozen=True)
class FedTarget:
    outcome: str
    meeting_date: date | None
    semantics_confident: bool
    reason: str


@dataclass(frozen=True)
class FedValidation:
    train_size: int
    test_size: int
    baseline_log_loss: float
    transition_log_loss: float
    baseline_multiclass_brier: float
    transition_multiclass_brier: float
    passed: bool

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass(frozen=True)
class FedShadow:
    available: bool
    probability: float | None
    distribution: dict[str, float] | None
    confidence: float
    reason_code: str | None
    diagnostics: dict


def _target_dict(target: FedTarget) -> dict:
    return {
        "outcome": target.outcome,
        "meeting_date": target.meeting_date.isoformat() if target.meeting_date else None,
        "semantics_confident": target.semantics_confident,
        "reason": target.reason,
    }


def _meetings() -> tuple[FedMeeting, ...]:
    # Scheduled meetings, not just rate-change dates.  Action rows are
    # derived from the official source above; zero means the target range was
    # maintained at that scheduled decision.
    rows = (
        (2021, ("01-27", "03-17", "04-28", "06-16", "07-28", "09-22", "11-03", "12-15"), ("UNCHANGED",) * 8),
        (2022, ("01-26", "03-16", "05-04", "06-15", "07-27", "09-21", "11-02", "12-14"), ("UNCHANGED", "HIKE_25", "HIKE_50_PLUS", "HIKE_50_PLUS", "HIKE_50_PLUS", "HIKE_50_PLUS", "HIKE_50_PLUS", "HIKE_50_PLUS")),
        (2023, ("02-01", "03-22", "05-03", "06-14", "07-26", "09-20", "11-01", "12-13"), ("HIKE_25", "HIKE_25", "HIKE_25", "UNCHANGED", "HIKE_25", "UNCHANGED", "UNCHANGED", "UNCHANGED")),
        (2024, ("01-31", "03-20", "05-01", "06-12", "07-31", "09-18", "11-07", "12-18"), ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED", "CUT_50_PLUS", "CUT_25", "CUT_25")),
        (2025, ("01-29", "03-19", "05-07", "06-18", "07-30", "09-17", "10-29", "12-10"), ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED", "CUT_25", "CUT_25", "CUT_25")),
    )
    return tuple(FedMeeting(date.fromisoformat(f"{year}-{day}"), action) for year, dates, actions in rows for day, action in zip(dates, actions, strict=True))


def training_dataset() -> tuple[FedMeeting, ...]:
    return _meetings()


def parse_fed_target(question: str, resolution_text: str | None) -> FedTarget:
    text = f"{question} {resolution_text or ''}".lower()
    meeting = None
    found = re.search(r"(?:september|sep(?:tember)?)\s+(20\d{2})", text)
    if found:
        meeting = date(int(found.group(1)), 9, 16)
    if "unchanged" in text or "no change" in text:
        return FedTarget("UNCHANGED", meeting, True, "exact no-change bucket")
    if re.search(r"(?:increase|hike|raise).{0,40}(?:50\+|50 or more|at least 50)", text):
        return FedTarget("HIKE_50_PLUS", meeting, True, "exact hike 50+ bucket")
    if re.search(r"(?:increase|hike|raise).{0,40}25\s*(?:bp|bps|basis)", text):
        return FedTarget("HIKE_25", meeting, True, "exact hike 25 bucket")
    if re.search(r"(?:decrease|cut|lower).{0,40}(?:50\+|50 or more|at least 50)", text):
        return FedTarget("CUT_50_PLUS", meeting, True, "exact cut 50+ bucket")
    if re.search(r"(?:decrease|cut|lower).{0,40}25\s*(?:bp|bps|basis)", text):
        return FedTarget("CUT_25", meeting, True, "exact cut 25 bucket")
    return FedTarget("", meeting, False, "exact FOMC outcome bucket could not be parsed")


def _distribution(rows: tuple[FedMeeting, ...], previous_action: str | None) -> dict[str, float]:
    # Laplace smoothing makes the distribution explicit and deterministic;
    # it is not an invented fallback.  Unknown prior action uses the stated
    # unconditional baseline only.
    if previous_action is None:
        counts = Counter(row.action for row in rows)
    else:
        counts = Counter(row.action for i, row in enumerate(rows) if i and rows[i - 1].action == previous_action)
        if not counts:
            counts = Counter(row.action for row in rows)
    denominator = sum(counts.values()) + len(OUTCOMES)
    return {outcome: (counts[outcome] + 1) / denominator for outcome in OUTCOMES}


def validate_model() -> FedValidation:
    rows = training_dataset()
    split = 24  # 2021--23 train, 2024--25 true later holdout
    train, test = rows[:split], rows[split:]
    baseline_ll = transition_ll = baseline_brier = transition_brier = 0.0
    for index, row in enumerate(test, start=split):
        baseline = _distribution(train, None)
        transition = _distribution(train, rows[index - 1].action)
        baseline_ll -= math.log(baseline[row.action])
        transition_ll -= math.log(transition[row.action])
        baseline_brier += sum((baseline[o] - float(o == row.action)) ** 2 for o in OUTCOMES)
        transition_brier += sum((transition[o] - float(o == row.action)) ** 2 for o in OUTCOMES)
    n = len(test)
    result = FedValidation(split, n, baseline_ll / n, transition_ll / n, baseline_brier / n, transition_brier / n, transition_ll <= baseline_ll and transition_brier <= baseline_brier)
    return result


def predict_shadow(
    question: str,
    resolution_text: str | None,
    snapshot: MacroSnapshot | None,
    policy_decision: FedPolicyDecision | None = None,
) -> FedShadow:
    target = parse_fed_target(question, resolution_text)
    validation = validate_model()
    if not target.semantics_confident:
        return FedShadow(False, None, None, 0.0, "SEMANTICS_UNCERTAIN", {"target": _target_dict(target), "validation": validation.as_dict()})
    if policy_decision is None:
        return FedShadow(False, None, None, 0.0, "FOMC_PRIOR_POLICY_ACTION_UNAVAILABLE", {"target": _target_dict(target), "validation": validation.as_dict()})
    if target.meeting_date is not None and policy_decision.decision_date >= target.meeting_date:
        return FedShadow(False, None, None, 0.0, "FOMC_PRIOR_POLICY_ACTION_STALE", {"target": _target_dict(target), "validation": validation.as_dict(), "policy_decision": policy_decision.as_dict()})
    if not validation.passed:
        return FedShadow(False, None, None, 0.0, "MODEL_NOT_VALIDATED", {"target": _target_dict(target), "validation": validation.as_dict()})
    # The model is trained only on the fixed 2021--2025 dataset.  The live
    # policy action is the current value of its single validated feature, not
    # an additional training observation and never a market-price proxy.
    prior = policy_decision.action
    distribution = _distribution(training_dataset(), prior)
    confidence = round(min(45.0, 20.0 + len(training_dataset()) * 0.55), 1)
    return FedShadow(True, distribution[target.outcome], distribution, confidence, None, {"target": _target_dict(target), "prior_action": prior, "policy_decision": policy_decision.as_dict(), "dataset_id": DATASET_ID, "dataset_version": DATASET_VERSION, "model_id": MODEL_ID, "model_version": MODEL_VERSION, "model_confidence": confidence, "validation": validation.as_dict(), "sources": [FED_OPEN_MARKET_SOURCE, FED_CALENDAR_SOURCE, policy_decision.source_url], "sample_size": len(training_dataset()), "feature_list": ["previous_fomc_action"], "non_model_macro_snapshot": snapshot.as_dict() if snapshot else None, "point_in_time_flag": "live prior action is official statement; macro vintages are not model features"})


def registry_records() -> tuple[dict, dict]:
    """Immutable metadata payloads for the storage model/dataset registry."""
    validation = validate_model()
    timestamp = datetime.now(UTC).isoformat()
    dataset = {
        "dataset_id": DATASET_ID, "version": DATASET_VERSION, "archetype": "MACRO_POLICY",
        "extracted_at": timestamp, "source_lineage": [FED_OPEN_MARKET_SOURCE, FED_CALENDAR_SOURCE],
        "filters": {"meeting_years": [2021, 2025], "scheduled_meetings_only": True},
        "sample_count": len(training_dataset()),
        "metadata": {"outcomes": OUTCOMES, "macro_vintages": "not included; prior-action model only"},
    }
    model = {
        "model_id": MODEL_ID, "version": MODEL_VERSION, "archetype": "MACRO_POLICY",
        "dataset_id": DATASET_ID, "dataset_version": DATASET_VERSION, "trained_at": timestamp,
        "metrics": validation.as_dict(), "feature_list": ["previous_fomc_action"],
        "active": validation.passed,
    }
    return dataset, model
