"""Historical submodel — the V1 base-rate blend, now packaged as one
independent ensemble member instead of the whole engine. Looks at
previously *resolved* markets in the same category and provider, takes
their observed YES rate, and reports that as an estimate with a weight that
grows with sample size (capped so a handful of cases can never dominate the
ensemble on their own).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .classification import MarketClassification, classify_market
from .semantics import MarketProposition, parse_market_proposition
from .types import SubmodelEstimate

MIN_COMPARABLE_SAMPLE = 3  # kept for backward-compat imports; graduated tiers below replace the old binary gate
MAX_HISTORY_WEIGHT = 0.6

# Graduated confidence tiers (spec: no pseudo-precise probability from a
# handful of cases). Each tier caps the maximum ensemble weight the
# historical baseline is allowed to carry — a bigger, more trustworthy
# sample earns more say, never a fixed weight regardless of sample size.
TIER_UNAVAILABLE = "unavailable"  # 0-2 cases
TIER_VERY_LOW = "very_low"  # 3-9 cases
TIER_LIMITED = "limited"  # 10-29 cases
TIER_USABLE = "usable"  # 30+ cases

_TIER_MAX_WEIGHT = {TIER_VERY_LOW: 0.15, TIER_LIMITED: 0.35, TIER_USABLE: MAX_HISTORY_WEIGHT}
_TIER_LABEL_DE = {
    TIER_VERY_LOW: "sehr geringe Konfidenz", TIER_LIMITED: "eingeschränkte Konfidenz", TIER_USABLE: "belastbar",
}


def _confidence_tier(sample_size: int) -> str:
    if sample_size < 3:
        return TIER_UNAVAILABLE
    if sample_size < 10:
        return TIER_VERY_LOW
    if sample_size < 30:
        return TIER_LIMITED
    return TIER_USABLE


# ---------------------------------------------------------------------------
# Phase D: similarity-weighted comparable-case scorer (D3/D4)
# ---------------------------------------------------------------------------
#
# Replaces plain "same category" grouping with a graded similarity score per
# candidate, so a market from the same category but a totally different
# predicate/actor/timeframe counts for less than one that also matches on
# event_type, entities, wording and timing. Weights (documented here, not
# hidden in the code) were chosen by rough decreasing order of how much each
# signal actually constrains "is this the same kind of bet":
#
#   category_match          0.30  - coarsest signal (Phase C taxonomy), but
#                                    cheap and reliable, so it anchors the score.
#   event_type_match        0.25  - much more specific than category (Phase A
#                                    predicate family, e.g. "office_departure")
#                                    when both target and candidate have one.
#   entity_overlap          0.20  - Jaccard overlap of extracted proper-noun
#                                    entities (same people/orgs/places involved).
#   token_overlap           0.12  - cheap token-overlap semantic-similarity
#                                    proxy over the question text (no
#                                    embeddings, no paid calls) — catches
#                                    topical closeness event_type/category miss.
#   time_horizon_similarity 0.08  - how similar the markets' resolution
#                                    windows are (a 3-day market isn't a great
#                                    comparable for a 2-year one).
#   resolution_structure    0.03  - both binary YES/NO, both cleanly parsed
#                                    (CLEAR) vs ambiguous — comparability of
#                                    the resolution mechanism itself.
#   geographic_similarity   0.02  - matching parsed location, when present.
#
# These sum to 1.0. All are cheap, deterministic, rule-based comparisons —
# no ML model, no embeddings API, no paid calls of any kind.
_WEIGHT_CATEGORY = 0.30
_WEIGHT_EVENT_TYPE = 0.25
_WEIGHT_ENTITY = 0.20
_WEIGHT_TOKEN = 0.12
_WEIGHT_TIME_HORIZON = 0.08
_WEIGHT_RESOLUTION_STRUCTURE = 0.03
_WEIGHT_GEO = 0.02

_TOKEN_STOPWORDS = frozenset(
    {"the", "a", "an", "of", "to", "in", "on", "by", "for", "and", "or", "is", "are", "will",
     "be", "as", "at", "with", "from", "this", "that", "it", "its", "if", "than", "before",
     "after", "does", "do", "did", "has", "have", "had", "not", "no", "yes"}
)


def _tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    words = {w.lower() for w in text.replace("?", " ").replace(",", " ").split()}
    return {w for w in words if len(w) > 2 and w not in _TOKEN_STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _days_to_resolution(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        from datetime import datetime

        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        return abs((e - s).total_seconds() / 86400)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class ComparableCandidate:
    """One historical row to be scored against a target proposition. Built
    from the `markets`/`market_resolutions` tables (see
    `_load_comparable_candidates`) or constructed directly by callers/tests."""

    market_id: str
    question: str
    category: str | None
    event_type: str | None
    entities: tuple[str, ...]
    proposition_status: str | None
    location: str | None
    start_date: str | None
    end_date: str | None
    winning_outcome: str | None
    resolution_status: str  # 'resolved' | 'cancelled' | 'invalid' | 'disputed'

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id, "question": self.question, "category": self.category,
            "event_type": self.event_type, "entities": list(self.entities),
            "proposition_status": self.proposition_status, "location": self.location,
            "start_date": self.start_date, "end_date": self.end_date,
            "winning_outcome": self.winning_outcome, "resolution_status": self.resolution_status,
        }


def _score_candidate(
    target_proposition: MarketProposition,
    target_classification: MarketClassification,
    target_entities: set[str],
    target_tokens: set[str],
    candidate: ComparableCandidate,
) -> float:
    score = 0.0

    if candidate.category and target_classification.category and candidate.category == target_classification.category:
        score += _WEIGHT_CATEGORY

    if candidate.event_type and target_proposition.event_type and candidate.event_type == target_proposition.event_type:
        score += _WEIGHT_EVENT_TYPE

    entity_sim = _jaccard(target_entities, set(candidate.entities))
    score += _WEIGHT_ENTITY * entity_sim

    token_sim = _jaccard(target_tokens, _tokenize(candidate.question))
    score += _WEIGHT_TOKEN * token_sim

    target_horizon = _days_to_resolution(target_proposition.start_time, target_proposition.deadline)
    candidate_horizon = _days_to_resolution(candidate.start_date, candidate.end_date)
    if target_horizon is not None and candidate_horizon is not None:
        longer = max(target_horizon, candidate_horizon, 1.0)
        shorter = min(target_horizon, candidate_horizon)
        score += _WEIGHT_TIME_HORIZON * (shorter / longer)

    if candidate.proposition_status and candidate.proposition_status == target_proposition.proposition_status:
        score += _WEIGHT_RESOLUTION_STRUCTURE

    if target_proposition.location and candidate.location and target_proposition.location == candidate.location:
        score += _WEIGHT_GEO

    return round(score, 6)


def find_comparable_cases(
    target_proposition: MarketProposition,
    target_classification: MarketClassification,
    candidates: list[ComparableCandidate],
) -> list[tuple[ComparableCandidate, float]]:
    """Score every candidate historical market against the target
    proposition/classification and return (candidate, similarity_score)
    pairs sorted by descending score. Does not filter by resolution status —
    callers that want to compute a YES/NO baseline must filter to
    `resolution_status == "resolved"` themselves (CANCELLED/INVALID/DISPUTED
    markets are never valid YES/NO training labels)."""
    # Entities come from the target's own extracted proper nouns (subject/
    # object of the parsed proposition), not the classifier's keyword-match
    # signals — this keeps entity overlap comparing "who/what" rather than
    # "which keywords fired".
    target_entities = _target_entities_from_proposition(target_proposition)
    target_tokens = _tokenize(target_proposition.yes_condition) | _tokenize(target_proposition.subject)

    scored = [
        (candidate, _score_candidate(target_proposition, target_classification, target_entities, target_tokens, candidate))
        for candidate in candidates
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def _target_entities_from_proposition(proposition: MarketProposition) -> set[str]:
    entities: set[str] = set()
    if proposition.subject:
        entities.add(proposition.subject)
    if proposition.object:
        entities.add(proposition.object)
    return entities


def _load_comparable_candidates(conn: sqlite3.Connection, provider: str | None = None) -> list[ComparableCandidate]:
    """Pull every historically-resolved-or-terminal market (any status —
    resolved/cancelled/invalid/disputed) with its Phase A/C structured data
    for scoring. Terminal-but-non-resolved rows are included so
    find_comparable_cases can show them (e.g. for diagnostics) but
    compute_weighted_baseline explicitly excludes anything except
    'resolved' from the YES/NO baseline math."""
    query = """
        SELECT m.market_id, m.question, m.classified_category, m.event_type,
               m.entities_json, m.proposition_json, m.start_date, m.end_date,
               mr.winning_outcome, mr.status
        FROM markets m
        JOIN market_resolutions mr ON mr.provider = m.provider AND mr.provider_market_id = m.provider_market_id
    """
    params: tuple = ()
    if provider:
        query += " WHERE m.provider = ?"
        params = (provider,)

    out: list[ComparableCandidate] = []
    for row in conn.execute(query, params).fetchall():
        market_id, question, category, event_type, entities_json, proposition_json, start_date, end_date, winning_outcome, status = row
        try:
            entities = tuple(json.loads(entities_json)) if entities_json else ()
        except (json.JSONDecodeError, TypeError):
            entities = ()
        proposition_status = None
        location = None
        if proposition_json:
            try:
                parsed = json.loads(proposition_json)
                proposition_status = parsed.get("proposition_status")
                location = parsed.get("location")
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(
            ComparableCandidate(
                market_id=market_id, question=question or "", category=category, event_type=event_type,
                entities=entities, proposition_status=proposition_status, location=location,
                start_date=start_date, end_date=end_date, winning_outcome=winning_outcome,
                resolution_status=status,
            )
        )
    return out


# --- K2: historical baseline uncertainty interval --------------------------
# Wilson score interval, not a naive normal-approximation (Wald) interval:
# the Wald interval (p +/- z*sqrt(p(1-p)/n)) badly undercovers at small n or
# when p is near 0/1 — exactly the "4 cases must not look as trustworthy as
# 400" regime this feature exists for. Wilson score is the standard
# textbook fix (correct even at very small n, never produces bounds outside
# [0,1]) and needs only n (here: Kish's effective sample size, ESS, so a
# handful of near-duplicate high-weight cases don't buy an artificially
# narrow interval) and p (the weighted YES mass) as inputs — no extra
# hyperparameters to justify, unlike a Beta-posterior credible interval
# which would require picking a prior (alpha, beta) that isn't otherwise
# motivated anywhere else in this codebase.
_WILSON_Z = 1.96  # 95% interval


def _wilson_score_interval(p: float, n: float) -> tuple[float, float]:
    """Wilson score interval for a proportion `p` estimated from an
    (effective) sample size `n`. Returns (lower, upper), both in [0, 1].
    n <= 0 returns the maximally uninformative (0.0, 1.0) interval."""
    if n <= 0:
        return 0.0, 1.0
    z = _WILSON_Z
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half_width = (z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)) / denom
    lower = max(0.0, center - half_width)
    upper = min(1.0, center + half_width)
    return round(lower, 4), round(upper, 4)


@dataclass(frozen=True)
class WeightedBaselineResult:
    """Result of `compute_weighted_baseline`. `baseline_yes_probability` is
    None when there are zero usable (resolved, YES/NO) comparable cases.

    K2 additions: `lower_bound`/`upper_bound` are a 95% Wilson score
    interval around `baseline_yes_probability`, computed from the weighted
    YES mass and `effective_sample_size` (Kish's ESS as the effective N).
    `uncertainty_width` is `upper_bound - lower_bound` — the single number
    that visibly shrinks as real comparable evidence accumulates, so a
    4-case baseline reports a visibly wider (less trustworthy-looking)
    interval than a 400-case one at the same point estimate.
    `historical_probability` is an alias of `baseline_yes_probability` kept
    for the K2 field-name spec (`historical_probability`,
    `effective_sample_size`, `lower_bound`, `upper_bound`,
    `uncertainty_width`) without breaking the existing
    `baseline_yes_probability` name other callers already read."""

    baseline_yes_probability: float | None
    total_weight: float
    effective_sample_size: float
    case_count: int
    tier: str
    detail: str
    excluded_non_binary_count: int = 0
    lower_bound: float | None = None
    upper_bound: float | None = None
    uncertainty_width: float | None = None
    # I3 (additive): the actual comparable cases that fed the weighted
    # baseline above (resolved, binary YES/NO, positive similarity weight),
    # sorted by descending similarity — so the UI can show real
    # question/similarity/outcome/weight rows instead of only the
    # aggregate number. Empty on the legacy category-equality path (which
    # has no per-case similarity score at all).
    top_comparable_cases: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def historical_probability(self) -> float | None:
        return self.baseline_yes_probability


def compute_weighted_baseline(
    comparable_cases_with_scores: list[tuple[ComparableCandidate, float]],
) -> WeightedBaselineResult:
    """Similarity-weighted historical base rate:

        baseline = sum(similarity_weight * outcome) / sum(similarity_weight)

    where outcome is 1.0 for a YES resolution and 0.0 for NO. Effective
    sample size uses Kish's formula: ESS = (sum w)^2 / sum(w^2) — a handful
    of near-identical (high-weight) cases can still yield a small ESS if
    their weights are wildly unequal, which is exactly the "don't be
    pseudo-precise" property we want. CANCELLED/INVALID/DISPUTED markets
    (resolution_status != 'resolved') are never counted as YES/NO training
    labels — they're skipped and counted in `excluded_non_binary_count`."""
    usable: list[tuple[float, float]] = []  # (weight, outcome)
    usable_cases: list[dict] = []  # I3: same rows, with display fields, for top_comparable_cases
    excluded = 0
    for candidate, weight in comparable_cases_with_scores:
        if candidate.resolution_status != "resolved":
            excluded += 1
            continue
        if not candidate.winning_outcome or candidate.winning_outcome.lower() not in ("yes", "no"):
            excluded += 1
            continue
        if weight <= 0:
            continue
        outcome = 1.0 if candidate.winning_outcome.lower() == "yes" else 0.0
        usable.append((weight, outcome))
        usable_cases.append(
            {
                "market_id": candidate.market_id, "question": candidate.question,
                "similarity_score": weight, "outcome": candidate.winning_outcome,
                "resolution_status": candidate.resolution_status,
            }
        )

    if not usable:
        return WeightedBaselineResult(
            baseline_yes_probability=None, total_weight=0.0, effective_sample_size=0.0,
            case_count=0, tier=TIER_UNAVAILABLE,
            detail="Keine gewichtbaren, aufgelösten YES/NO-Vergleichsfälle gefunden.",
            excluded_non_binary_count=excluded,
        )

    total_weight = sum(w for w, _ in usable)
    sum_sq_weight = sum(w * w for w, _ in usable)
    weighted_outcome_sum = sum(w * o for w, o in usable)
    baseline = weighted_outcome_sum / total_weight if total_weight > 0 else None
    ess = (total_weight**2) / sum_sq_weight if sum_sq_weight > 0 else 0.0

    # Reuse the graduated tier thresholds (spec: don't remove the outer
    # confidence-capping mechanism) but gate on *effective* sample size
    # rather than raw case count, since a pile of low-similarity cases
    # should not buy the same confidence as fewer, tightly comparable ones.
    if ess < 3:
        tier = TIER_UNAVAILABLE
    elif ess < 10:
        tier = TIER_VERY_LOW
    elif ess < 30:
        tier = TIER_LIMITED
    else:
        tier = TIER_USABLE

    lower, upper = _wilson_score_interval(baseline, ess)
    width = round(upper - lower, 4)

    for case in usable_cases:
        case["weight_share"] = round(case["similarity_score"] / total_weight, 4) if total_weight > 0 else None
    usable_cases.sort(key=lambda c: c["similarity_score"], reverse=True)
    top_cases = tuple(usable_cases[:10])

    return WeightedBaselineResult(
        baseline_yes_probability=round(baseline, 4) if baseline is not None else None,
        total_weight=round(total_weight, 4),
        effective_sample_size=round(ess, 2),
        case_count=len(usable),
        tier=tier,
        detail=(
            f"{len(usable)} gewichtete Vergleichsfälle (Gesamtgewicht={total_weight:.2f}, "
            f"effektive Stichprobengröße={ess:.2f}), gewichteter Basiswert={baseline:.2%} "
            f"(Konfidenzstufe: {tier}). 95%-Wilson-Intervall: [{lower:.2%}, {upper:.2%}] "
            f"(Breite={width:.2%})."
        ),
        excluded_non_binary_count=excluded,
        lower_bound=lower,
        upper_bound=upper,
        uncertainty_width=width,
        top_comparable_cases=top_cases,
    )


def compute_history_estimate(
    conn: sqlite3.Connection,
    category: str | None,
    provider: str,
    question: str | None = None,
    resolution_text: str | None = None,
) -> tuple[SubmodelEstimate, int, float | None, WeightedBaselineResult | None]:
    """Returns (estimate, comparable_sample_size, observed_yes_rate, uncertainty).

    `uncertainty` is the full WeightedBaselineResult (Kish ESS, Wilson
    lower/upper/width) when the similarity-weighted path ran (`question`
    supplied), so K1/J2 composites can reuse the *real* K2 numbers instead
    of recomputing them — None on the legacy category-equality path, which
    has no ESS/Wilson-interval concept at all (honestly reported, not
    faked).

    When `question` is supplied, this uses the Phase D similarity-weighted
    comparable-case scorer (find_comparable_cases + compute_weighted_baseline)
    instead of plain category-equality grouping: the proposition/
    classification are parsed from `question`/`resolution_text`, candidates
    are pulled from the full resolved-market history (not just an exact
    category match) and scored by similarity, and the weighted baseline
    plus its effective sample size determine the confidence tier below.
    Called without `question` (the historical/back-compat call shape), it
    falls back to the original plain category-equality grouping unchanged —
    existing callers/tests that don't have a question string keep exactly
    their old behavior."""
    if question:
        return _compute_history_estimate_weighted(conn, category, provider, question, resolution_text)
    est, n, rate = _compute_history_estimate_legacy(conn, category, provider)
    return est, n, rate, None


def _compute_history_estimate_weighted(
    conn: sqlite3.Connection,
    category: str | None,
    provider: str,
    question: str,
    resolution_text: str | None,
) -> tuple[SubmodelEstimate, int, float | None, WeightedBaselineResult | None]:
    target_proposition = parse_market_proposition(question, resolution_text)
    target_classification = classify_market(question, resolution_text, target_proposition)
    candidates = _load_comparable_candidates(conn, provider=provider)
    scored = find_comparable_cases(target_proposition, target_classification, candidates)
    result = compute_weighted_baseline(scored)

    sample_size = result.case_count
    observed_yes_rate = result.baseline_yes_probability

    if result.tier == TIER_UNAVAILABLE:
        return (
            SubmodelEstimate(
                name="history", estimated_yes_probability=observed_yes_rate, weight=0.0, available=False,
                detail=result.detail,
            ),
            sample_size, observed_yes_rate, result,
        )

    tier_cap = _TIER_MAX_WEIGHT[result.tier]
    weight = min(tier_cap, result.effective_sample_size / 50)
    return (
        SubmodelEstimate(
            name="history", estimated_yes_probability=observed_yes_rate, weight=weight, available=True,
            detail=result.detail,
        ),
        sample_size, observed_yes_rate, result,
    )


def _compute_history_estimate_legacy(
    conn: sqlite3.Connection, category: str | None, provider: str
) -> tuple[SubmodelEstimate, int, float | None]:
    """Returns (estimate, comparable_sample_size, observed_yes_rate)."""
    rows = conn.execute(
        """
        SELECT mr.winning_outcome
        FROM market_resolutions mr
        JOIN markets m ON m.provider = mr.provider AND m.provider_market_id = mr.provider_market_id
        WHERE mr.status = 'resolved' AND m.category = ? AND m.provider = ?
        """,
        (category, provider),
    ).fetchall()
    sample_size = len(rows)

    if sample_size == 0:
        return (
            SubmodelEstimate(
                name="history", estimated_yes_probability=None, weight=0.0, available=False,
                detail=f"Keine historisch aufgelösten Vergleichsmärkte in Kategorie '{category}' gefunden.",
            ),
            0, None,
        )

    yes_count = sum(1 for r in rows if r[0] and r[0].lower() == "yes")
    observed_yes_rate = round(yes_count / sample_size, 4)
    tier = _confidence_tier(sample_size)

    if tier == TIER_UNAVAILABLE:
        return (
            SubmodelEstimate(
                name="history", estimated_yes_probability=observed_yes_rate,
                weight=0.0, available=False,
                detail=(
                    f"{sample_size} vergleichbare(r) Fall/Fälle gefunden (< 3) — "
                    "zu wenig Stichprobe für ein eigenständiges Ensemble-Gewicht (Stufe: unavailable)."
                ),
            ),
            sample_size, observed_yes_rate,
        )

    # Weight scales with sample size within the tier's cap — so a 9-case
    # very_low sample still carries less weight than a 29-case limited one,
    # rather than every sample within a tier getting the exact same weight.
    tier_cap = _TIER_MAX_WEIGHT[tier]
    weight = min(tier_cap, sample_size / 50)
    return (
        SubmodelEstimate(
            name="history", estimated_yes_probability=observed_yes_rate, weight=weight, available=True,
            detail=(
                f"{sample_size} historisch aufgelöste(r) Markt/Märkte in Kategorie '{category}' gefunden, "
                f"davon {yes_count} mit Ausgang YES ({observed_yes_rate:.0%}). "
                f"Konfidenzstufe: {_TIER_LABEL_DE[tier]} ({sample_size} Fälle)."
            ),
        ),
        sample_size, observed_yes_rate,
    )
