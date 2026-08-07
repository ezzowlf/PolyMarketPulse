"""Phase D3/D4: similarity-weighted comparable-case scorer and weighted
historical baseline (history.py's find_comparable_cases /
compute_weighted_baseline), replacing plain category-equality grouping."""

from __future__ import annotations

from polymarketpulse.prediction.classification import classify_market
from polymarketpulse.prediction.history import (
    TIER_LIMITED,
    TIER_UNAVAILABLE,
    TIER_USABLE,
    TIER_VERY_LOW,
    ComparableCandidate,
    compute_weighted_baseline,
    find_comparable_cases,
)
from polymarketpulse.prediction.semantics import parse_market_proposition


def _candidate(
    market_id: str,
    question: str,
    category: str | None,
    event_type: str | None,
    entities: tuple[str, ...],
    winning_outcome: str | None,
    resolution_status: str = "resolved",
    proposition_status: str = "CLEAR",
) -> ComparableCandidate:
    return ComparableCandidate(
        market_id=market_id, question=question, category=category, event_type=event_type,
        entities=entities, proposition_status=proposition_status, location=None,
        start_date=None, end_date=None, winning_outcome=winning_outcome,
        resolution_status=resolution_status,
    )


def _target():
    question = "Will Trump resign as President by August 31?"
    proposition = parse_market_proposition(question, None)
    classification = classify_market(question, None, proposition)
    return proposition, classification


# --- find_comparable_cases: similarity ranking ------------------------------


def test_same_category_and_event_type_and_entities_scores_higher_than_category_only() -> None:
    proposition, classification = _target()
    assert classification.category is not None

    tightly_comparable = _candidate(
        "c1", "Will Putin resign as President by December 31, 2026?", classification.category,
        "office_departure", ("Trump",), "No",
    )
    category_only = _candidate(
        "c2", "Will a new trade deal be signed this year?", classification.category,
        None, (), "Yes",
    )

    scored = find_comparable_cases(proposition, classification, [tightly_comparable, category_only])
    scores = {c.market_id: score for c, score in scored}

    assert scores["c1"] > scores["c2"]


def test_scores_sorted_descending() -> None:
    proposition, classification = _target()
    high = _candidate("high", "Will Trump resign?", classification.category, "office_departure", ("Trump",), "Yes")
    low = _candidate("low", "Will the price of eggs rise?", "OTHER", None, (), "No")
    scored = find_comparable_cases(proposition, classification, [low, high])
    assert [c.market_id for c, _ in scored] == ["high", "low"]


def test_unrelated_category_scores_near_zero() -> None:
    proposition, classification = _target()
    unrelated = _candidate("u1", "Will BTC close above $100k?", "CRYPTO", None, (), "Yes")
    scored = find_comparable_cases(proposition, classification, [unrelated])
    assert scored[0][1] < 0.05


# --- compute_weighted_baseline: math ----------------------------------------


def test_weighted_baseline_hand_computed_example() -> None:
    # weight=1.0 -> outcome 1 (Yes), weight=0.5 -> outcome 0 (No), weight=0.5 -> outcome 1 (Yes)
    # baseline = (1.0*1 + 0.5*0 + 0.5*1) / (1.0 + 0.5 + 0.5) = 1.5 / 2.0 = 0.75
    cases = [
        (_candidate("a", "q", "POLITICS", None, (), "Yes"), 1.0),
        (_candidate("b", "q", "POLITICS", None, (), "No"), 0.5),
        (_candidate("c", "q", "POLITICS", None, (), "Yes"), 0.5),
    ]
    result = compute_weighted_baseline(cases)
    assert result.baseline_yes_probability == 0.75
    assert result.total_weight == 2.0
    # ESS = (sum w)^2 / sum(w^2) = (2.0)^2 / (1.0 + 0.25 + 0.25) = 4.0 / 1.5 = 2.6667
    assert result.effective_sample_size == round(4.0 / 1.5, 2)


def test_effective_sample_size_reflects_weight_concentration() -> None:
    # Ten cases but one dominant weight -> ESS should be much smaller than 10,
    # close to 1, since almost all the probability mass sits on one case.
    dominant = [(_candidate("dom", "q", "POLITICS", None, (), "Yes"), 100.0)]
    minor = [(_candidate(f"m{i}", "q", "POLITICS", None, (), "No"), 0.01) for i in range(9)]
    result = compute_weighted_baseline(dominant + minor)
    assert result.case_count == 10
    assert result.effective_sample_size < 2.0

    # Ten equally-weighted cases -> ESS should equal the case count exactly.
    equal = [(_candidate(f"e{i}", "q", "POLITICS", None, (), "Yes" if i % 2 == 0 else "No"), 1.0) for i in range(10)]
    equal_result = compute_weighted_baseline(equal)
    assert equal_result.effective_sample_size == 10.0


def test_no_pseudo_precision_from_tiny_effective_sample() -> None:
    # A single low-weight comparable case must not be reported at the
    # 'usable' confidence tier just because a number came out.
    cases = [(_candidate("only", "q", "POLITICS", None, (), "Yes"), 0.1)]
    result = compute_weighted_baseline(cases)
    assert result.baseline_yes_probability == 1.0  # the raw math is fine...
    assert result.effective_sample_size < 3
    assert result.tier == TIER_UNAVAILABLE  # ...but the tier must gate it as unusable


def test_tier_thresholds_on_effective_sample_size() -> None:
    def _make(n: int, weight: float) -> list[tuple[ComparableCandidate, float]]:
        return [(_candidate(f"x{i}", "q", "POLITICS", None, (), "Yes"), weight) for i in range(n)]

    assert compute_weighted_baseline(_make(2, 1.0)).tier == TIER_UNAVAILABLE
    assert compute_weighted_baseline(_make(5, 1.0)).tier == TIER_VERY_LOW
    assert compute_weighted_baseline(_make(15, 1.0)).tier == TIER_LIMITED
    assert compute_weighted_baseline(_make(35, 1.0)).tier == TIER_USABLE


def test_empty_comparable_set_is_unavailable() -> None:
    result = compute_weighted_baseline([])
    assert result.baseline_yes_probability is None
    assert result.tier == TIER_UNAVAILABLE
    assert result.case_count == 0


# --- Never use CANCELLED/INVALID/DISPUTED as YES/NO training labels --------


def test_cancelled_invalid_disputed_never_counted_as_training_labels() -> None:
    cases = [
        (_candidate("resolved-yes", "q", "POLITICS", None, (), "Yes", resolution_status="resolved"), 1.0),
        (_candidate("cancelled", "q", "POLITICS", None, (), None, resolution_status="cancelled"), 1.0),
        (_candidate("invalid", "q", "POLITICS", None, (), None, resolution_status="invalid"), 1.0),
        (_candidate("disputed", "q", "POLITICS", None, (), None, resolution_status="disputed"), 1.0),
    ]
    result = compute_weighted_baseline(cases)
    assert result.case_count == 1
    assert result.baseline_yes_probability == 1.0
    assert result.excluded_non_binary_count == 3


def test_non_binary_winning_outcome_excluded_from_training_labels() -> None:
    # A multi-outcome market (e.g. "which team wins") resolving to a team
    # name rather than Yes/No must never be counted as a YES/NO label, even
    # if its resolution_status is 'resolved'.
    cases = [
        (_candidate("binary", "q", "SPORT_BASKETBALL", None, (), "Yes"), 1.0),
        (_candidate("multi-outcome", "q", "SPORT_BASKETBALL", None, (), "Celtics"), 1.0),
    ]
    result = compute_weighted_baseline(cases)
    assert result.case_count == 1
    assert result.excluded_non_binary_count == 1
