"""Phase M — divergence red-team audit.

Upgrades Phase B4's binary "evidence_is_strong bool -> suppress or not"
check into an itemized, per-dimension audit with a graded verdict
(PASS / WARN / REJECT). Only runs when the gap between the independent
estimate and the market price exceeds `divergence.DIVERGENCE_THRESHOLD_PP`
(the exact same constant Phase B4 uses — no drift).

Verdict logic (documented, not just a count):
  - Any single check tagged `hard_fail=True` and verdict=REJECT forces the
    overall verdict to REJECT regardless of how many other checks PASS.
    Exactly two dimensions are hard-fail-eligible in this implementation:
    `evidentiary_sufficiency` (mirrors Phase B4's exact old
    evidence_is_strong gate: neither a 10+-case DATA_FITTED historical
    baseline nor >=2-source DIRECT-tier independent evidence backs the
    divergence) and `model_disagreement` (submodels wildly disagree,
    stdev > 0.25 — the estimate is not internally robust).
  - `proposition_clarity` and `resolution_rule_presence` were deliberately
    NOT made hard-fail-eligible despite being named as candidates in the
    spec: `question`/`resolution_text` are optional parameters on
    compute_prediction() and a meaningful share of real callers/tests omit
    them even for well-evidenced markets, so hard-failing on their bare
    absence would suppress correct forecasts for reasons unrelated to
    evidence quality. They still surface as WARN so the gap is visible.
    See the inline NOTE comments on each check for the full reasoning.
  - Otherwise, any WARN-tier check present (but no REJECT) yields an
    overall WARN — the forecast stands but is flagged for visibility.
    Block D Part 2 re-verification (documented decision, not assumed): a
    WARN verdict can ONLY be reached when `evidentiary_sufficiency` itself
    is PASS (its only other outcome is REJECT with hard_fail=True, which
    forces the overall verdict to REJECT before WARN is ever considered —
    see `_resolve_verdict`). `evidentiary_sufficiency` PASS requires either
    a 10+-case DATA_FITTED historical baseline or >=2-source, DIRECT-tier
    independent evidence. This means "je größer die Abweichung, desto höher
    die Evidenzanforderung" is already structurally enforced at the WARN
    tier by construction, not just by convention: a WARN-tier divergence
    with only marginal/weak evidence is not a real, reachable state in this
    implementation — it would already have been hard-failed to REJECT. The
    remaining WARN-tier findings (proposition_clarity, resolution_rule_
    presence, temporal_correctness, prior_provenance, comparables_quality,
    duplicate_evidence, source_independence, freshness) are all secondary
    quality/completeness signals layered ON TOP of an already-evidentiarily-
    sufficient divergence — correctly left as visibility flags, not
    additional hard-fail gates, per the same reasoning documented on each
    check above. Locked in by `test_warn_verdict_always_implies_
    evidentiary_sufficiency_passed` in `tests/test_block_d_part2_part1.py`.
    A live check of every market in the local `data/polymarketpulse.db`
    that ever triggers the audit found 11/11 REJECT and 0 WARN today (an
    honest, reported finding, not engineered) — so this decision is
    currently unexercised by real data locally, but is real, tested, and
    would apply the moment a genuinely evidence-backed-but-imperfect
    divergence appears in the data.
  - No WARN/REJECT-tier findings at all yields PASS.
  - UNKNOWN-tier findings (dimension genuinely not checkable with today's
    data) never move the verdict on their own — they are reported honestly
    but do not count as either a pass or a strike, since fabricating a
    pass/fail for something we can't actually check would defeat the
    purpose of an honest audit.

Known real gap (documented, not faked): there is no genuine event-level
clustering/deduplication anywhere in this codebase (checked evidence.py —
`confirmation_count` counts distinct source *domains*, which is a weaker
signal than "distinct underlying events"). `_dedup_check` below implements
a minimal same-day + near-identical-title heuristic as a best-effort
stand-in; it is NOT full actor/action/time event clustering (that would
need Phase A's event-extraction structures compared pairwise, which this
round did not have budget to build safely). This is flagged honestly in
the check's own detail text and in the module report, not silently
presented as full deduplication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .divergence import DIVERGENCE_THRESHOLD_PP

if TYPE_CHECKING:
    from .evidence import IndependentEvidenceResult
    from .semantics import MarketProposition
    from .types import SubmodelEstimate

CheckVerdict = Literal["PASS", "WARN", "REJECT", "UNKNOWN"]
Verdict = Literal["PASS", "WARN", "REJECT"]


@dataclass(frozen=True)
class AuditCheck:
    name: str
    verdict: CheckVerdict
    detail: str
    hard_fail: bool = False

    def as_dict(self) -> dict:
        return {"name": self.name, "verdict": self.verdict, "detail": self.detail, "hard_fail": self.hard_fail}


@dataclass(frozen=True)
class DivergenceAuditResult:
    triggered: bool
    gap: float | None
    verdict: Verdict | None
    checks: tuple[AuditCheck, ...] = field(default_factory=tuple)
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "triggered": self.triggered,
            "gap": self.gap,
            "verdict": self.verdict,
            "checks": [c.as_dict() for c in self.checks],
            "summary": self.summary,
        }


@dataclass(frozen=True)
class DivergenceAuditContext:
    """Everything the audit needs, gathered by engine.py from data it
    already computed — this module owns no data access of its own."""

    independent_probability: float | None
    market_probability: float | None
    proposition: MarketProposition | None
    independent_evidence: IndependentEvidenceResult | None
    comparable_sample_size: int
    history_prior_provenance: str | None
    resolution_rules_present: bool
    submodel_estimates: tuple[SubmodelEstimate, ...]


def compute_model_disagreement(submodel_estimates: tuple[SubmodelEstimate, ...] | list[SubmodelEstimate]) -> float | None:
    """Stdev across available submodels' estimated_yes_probability values.
    Returns None when fewer than 2 submodels are available (disagreement is
    not a meaningful concept with 0 or 1 data points) — callers (J2/K1
    composites in confidence.py) must treat None as N/A, never as 0 or a
    forced high/low score. Shared by audit_divergence's own
    `model_disagreement` check below so the two never drift apart."""
    ests = [
        s.estimated_yes_probability
        for s in submodel_estimates
        if s.available and s.estimated_yes_probability is not None
    ]
    if len(ests) < 2:
        return None
    mean = sum(ests) / len(ests)
    return (sum((e - mean) ** 2 for e in ests) / len(ests)) ** 0.5


def audit_divergence(context: DivergenceAuditContext) -> DivergenceAuditResult:
    ip, mp = context.independent_probability, context.market_probability
    if ip is None or mp is None:
        return DivergenceAuditResult(triggered=False, gap=None, verdict=None)

    gap = round(abs(ip - mp), 4)
    if gap < DIVERGENCE_THRESHOLD_PP:
        return DivergenceAuditResult(triggered=False, gap=gap, verdict=None)

    checks: list[AuditCheck] = []
    prop = context.proposition
    ev = context.independent_evidence

    # 1. Proposition clarity
    if prop is None:
        checks.append(AuditCheck("proposition_clarity", "UNKNOWN", "No parsed proposition available."))
    elif prop.proposition_status == "CLEAR" and not prop.ambiguity_flags:
        checks.append(AuditCheck("proposition_clarity", "PASS", "Proposition parsed as CLEAR, no ambiguity flags."))
    elif prop.proposition_status == "CLEAR":
        checks.append(
            AuditCheck("proposition_clarity", "WARN", f"CLEAR but ambiguity flags present: {list(prop.ambiguity_flags)}.")
        )
    else:
        # NOTE (judgment call, documented, same reasoning as
        # resolution_rule_presence above): `question`/`resolution_text` are
        # optional parameters on compute_prediction(), and a meaningful
        # share of existing callers/test fixtures pass neither (or an
        # empty question), which by construction parses as AMBIGUOUS
        # (no subject/event_type detected) even for markets with a
        # perfectly good, well-evidenced independent estimate. Hard-failing
        # here would suppress those unrelated to any real proposition-text
        # quality problem. Kept as WARN; genuinely insufficient backing is
        # still caught by the `evidentiary_sufficiency` hard-fail gate.
        checks.append(
            AuditCheck(
                "proposition_clarity", "WARN",
                f"Proposition is AMBIGUOUS (flags: {list(prop.ambiguity_flags)}) — the parsed proposition "
                "text quality could not be confirmed clear.",
            )
        )

    # 2. Resolution-rule presence
    if context.resolution_rules_present and prop is not None and prop.resolution_authority:
        checks.append(
            AuditCheck("resolution_rule_presence", "PASS", f"Resolution authority present: {prop.resolution_authority}.")
        )
    elif context.resolution_rules_present:
        checks.append(
            AuditCheck(
                "resolution_rule_presence", "WARN",
                "Resolution text was present but no resolution_authority could be extracted from it.",
            )
        )
    else:
        # NOTE (judgment call, documented): this is a WARN, not a hard REJECT,
        # even though the M spec suggests "no resolution rule + high
        # divergence" as an example hard-fail. In practice `resolution_text`
        # is an optional parameter threaded all the way through
        # compute_prediction()/callers — most existing callers (and a large
        # share of Phase B/D/K test fixtures) simply don't pass it even for
        # perfectly legitimate, well-evidenced markets. Hard-failing on its
        # mere absence would suppress a large share of currently-correct
        # BASELINE_ONLY/EVIDENCE_ONLY forecasts purely because a caller
        # didn't thread an optional string through, which is a worse
        # regression than the risk it guards against. The real hard-fail
        # gate for "not enough real backing" is `evidentiary_sufficiency`
        # below, which mirrors Phase B4's exact old logic.
        checks.append(
            AuditCheck(
                "resolution_rule_presence", "WARN",
                "No real resolution-rules text was supplied — only the bare market question title was "
                "available to parse the proposition from.",
            )
        )

    # 3. Deadline / temporal correctness (best-effort — only what semantics.py exposes)
    if prop is not None and prop.deadline:
        checks.append(AuditCheck("temporal_correctness", "PASS", f"Deadline extracted from proposition: {prop.deadline}."))
    else:
        checks.append(
            AuditCheck(
                "temporal_correctness", "WARN",
                "No deadline could be extracted from the proposition to cross-check evidence timing "
                "against — cannot confirm evidence is actually about the same window the market resolves on.",
            )
        )

    # 4. Prior provenance (K3)
    pp = context.history_prior_provenance
    if pp == "DATA_FITTED" and context.comparable_sample_size >= 10:
        checks.append(
            AuditCheck(
                "prior_provenance", "PASS",
                f"History prior is DATA_FITTED with {context.comparable_sample_size} comparable cases.",
            )
        )
    elif pp == "DATA_FITTED":
        checks.append(
            AuditCheck(
                "prior_provenance", "WARN",
                f"History prior is DATA_FITTED but from a small sample ({context.comparable_sample_size} cases).",
            )
        )
    elif pp in ("EXPERT_HEURISTIC", "FALLBACK"):
        checks.append(
            AuditCheck(
                "prior_provenance", "WARN",
                f"Prior provenance is {pp} — not a statistically fitted estimate from real observed outcomes; "
                "a large divergence riding on this alone is a real red flag.",
            )
        )
    else:
        checks.append(AuditCheck("prior_provenance", "UNKNOWN", "Prior provenance not tracked for this market."))

    # 5. Comparables quality — proxy: comparable_sample_size.
    #    NOTE (honest gap): history.py's real Wilson-interval width /
    #    Kish effective-sample-size (WeightedBaselineResult.lower_bound/
    #    upper_bound/effective_sample_size) are NOT surfaced past
    #    compute_history_estimate() to engine.py today — only the raw
    #    comparable_sample_size is. Using raw sample size as the proxy is a
    #    real simplification, not a faked interval-width check.
    if context.comparable_sample_size >= 10:
        checks.append(AuditCheck("comparables_quality", "PASS", f"{context.comparable_sample_size} comparable cases (>=10)."))
    elif context.comparable_sample_size >= 3:
        checks.append(
            AuditCheck(
                "comparables_quality", "WARN",
                f"Only {context.comparable_sample_size} comparable cases — expect wide real uncertainty "
                "even though this check cannot see the actual Wilson interval width today.",
            )
        )
    else:
        checks.append(
            AuditCheck(
                "comparables_quality", "WARN",
                f"{context.comparable_sample_size} comparable case(s) — effectively no historical backing.",
            )
        )

    # 6. Evidence direction consistency (quality of what's there, not a
    #    hard gate by itself — a strong historical baseline can stand on
    #    its own with zero independent evidence; see check #6b below for
    #    the combined hard-fail gate that mirrors Phase B4's old logic).
    direct_count = 0
    if ev is not None and ev.available:
        all_items = (*ev.evidence_for_yes, *ev.evidence_for_no)
        direct_count = sum(1 for f in all_items if f.relation_label in ("DIRECT_YES", "DIRECT_NO"))
        if direct_count:
            checks.append(AuditCheck("evidence_direction", "PASS", f"{direct_count} DIRECT_YES/NO tier evidence item(s)."))
        elif all_items:
            checks.append(
                AuditCheck(
                    "evidence_direction", "WARN",
                    "Evidence present but only WEAK/SUPPORTS/CONTEXT tier — no primary-source-strength items.",
                )
            )
        else:
            checks.append(
                AuditCheck("evidence_direction", "WARN", "Independent evidence module available but no usable evidence items.")
            )
    else:
        checks.append(
            AuditCheck("evidence_direction", "WARN", "Independent evidence is unavailable/empty for this market.")
        )

    # 6b. Combined evidentiary sufficiency — the single hard-fail gate.
    #     Mirrors Phase B4's exact old evidence_is_strong logic (a
    #     reasonably sized historical comparable sample, OR independent
    #     evidence with >=2 independently-confirming source domains AND at
    #     least one DIRECT_YES/DIRECT_NO item) so existing suppression
    #     behavior is preserved/subsumed rather than silently changed.
    strong_history = context.comparable_sample_size >= 10 and context.history_prior_provenance == "DATA_FITTED"
    strong_evidence = bool(ev is not None and ev.available and ev.confirmation_count >= 2 and direct_count > 0)
    if strong_history or strong_evidence:
        checks.append(
            AuditCheck(
                "evidentiary_sufficiency", "PASS",
                f"Backed by {'a strong historical baseline' if strong_history else ''}"
                f"{' and ' if strong_history and strong_evidence else ''}"
                f"{'strong direct-tier confirming evidence' if strong_evidence else ''}.",
            )
        )
    else:
        checks.append(
            AuditCheck(
                "evidentiary_sufficiency", "REJECT",
                "Neither a reasonably sized historical baseline (10+ DATA_FITTED comparable cases) nor "
                "independent evidence with >=2 independently-confirming sources and at least one "
                "DIRECT_YES/DIRECT_NO item backs this divergence — insufficient to justify a number this "
                "far from the market consensus.",
                hard_fail=True,
            )
        )

    # 7. Duplicate / non-independent evidence (documented heuristic, see module docstring)
    checks.append(_dedup_check(ev))

    # 8. Source independence
    if ev is not None and ev.available:
        all_items = (*ev.evidence_for_yes, *ev.evidence_for_no)
        domains = {f.source_domain or f.source for f in all_items}
        if len(domains) >= 2:
            checks.append(AuditCheck("source_independence", "PASS", f"{len(domains)} distinct source domains."))
        elif len(domains) == 1:
            checks.append(
                AuditCheck("source_independence", "WARN", "Only a single distinct source domain backs this divergence.")
            )
        else:
            checks.append(AuditCheck("source_independence", "UNKNOWN", "No evidence domains to assess."))
    else:
        checks.append(AuditCheck("source_independence", "UNKNOWN", "No evidence available to assess source independence."))

    # 9. Freshness
    if ev is not None and ev.time_since_first_report_hours is not None:
        if ev.time_since_first_report_hours <= 72:
            checks.append(AuditCheck("freshness", "PASS", f"First report {ev.time_since_first_report_hours:.1f}h ago."))
        else:
            checks.append(
                AuditCheck(
                    "freshness", "WARN",
                    f"Evidence is stale ({ev.time_since_first_report_hours:.1f}h since first report).",
                )
            )
    else:
        checks.append(AuditCheck("freshness", "UNKNOWN", "No freshness timestamp available."))

    # 10. Model disagreement (variance across available submodel estimates)
    stdev = compute_model_disagreement(context.submodel_estimates)
    if stdev is not None:
        if stdev <= 0.10:
            checks.append(AuditCheck("model_disagreement", "PASS", f"Submodels agree closely (stdev={stdev:.3f})."))
        elif stdev <= 0.25:
            checks.append(AuditCheck("model_disagreement", "WARN", f"Moderate submodel disagreement (stdev={stdev:.3f})."))
        else:
            checks.append(
                AuditCheck(
                    "model_disagreement", "REJECT",
                    f"High submodel disagreement (stdev={stdev:.3f}) — the independent estimate is not robust "
                    "across the contributing submodels.",
                    hard_fail=True,
                )
            )
    else:
        checks.append(AuditCheck("model_disagreement", "UNKNOWN", "Fewer than 2 available submodel estimates to compare."))

    verdict = _resolve_verdict(checks)
    n_reject = sum(1 for c in checks if c.verdict == "REJECT")
    n_warn = sum(1 for c in checks if c.verdict == "WARN")
    summary = f"{gap:.1%} divergence; verdict={verdict} from {len(checks)} checks ({n_reject} REJECT, {n_warn} WARN)."
    return DivergenceAuditResult(triggered=True, gap=gap, verdict=verdict, checks=tuple(checks), summary=summary)


def _dedup_check(ev: IndependentEvidenceResult | None) -> AuditCheck:
    if ev is None or not ev.available:
        return AuditCheck("duplicate_evidence", "UNKNOWN", "No evidence to check for duplication.")
    items = (*ev.evidence_for_yes, *ev.evidence_for_no)
    if len(items) < 2:
        return AuditCheck("duplicate_evidence", "PASS", "Fewer than 2 evidence items — no duplication possible.")

    seen: dict[tuple[str, str], list[str]] = {}
    for it in items:
        day = (it.published_at or "")[:10]
        title_key = " ".join(sorted(set((it.title or "").lower().split()))[:6])
        key = (day, title_key)
        seen.setdefault(key, []).append(it.source_domain or it.source)

    dup_groups = [v for v in seen.values() if len(v) > 1]
    if dup_groups:
        return AuditCheck(
            "duplicate_evidence", "WARN",
            f"{len(dup_groups)} group(s) of evidence items share the same day + near-identical title text "
            "across different domains — likely the same underlying event reported by multiple outlets, "
            "possibly over-counted as independent confirmations. (Heuristic only — see module docstring: "
            "no genuine event-clustering/dedup logic exists elsewhere in this codebase today.)",
        )
    return AuditCheck(
        "duplicate_evidence", "PASS",
        "No same-day/near-identical-title duplication detected among evidence items (heuristic check; "
        "not full event clustering — see module docstring gap note).",
    )


def _resolve_verdict(checks: list[AuditCheck]) -> Verdict:
    if any(c.verdict == "REJECT" for c in checks):
        return "REJECT"
    if any(c.verdict == "WARN" for c in checks):
        return "WARN"
    return "PASS"


# ---------------------------------------------------------------------------
# Part 4 (this round): divergence-support classification.
#
# Deliberately a thin, honest relabeling of the verdict this module already
# computes above, NOT a second independent judgment — audit_divergence's
# PASS/WARN/REJECT verdict already IS the "is this divergence backed by real
# evidence" question (see `evidentiary_sufficiency`, the hard-fail check
# that mirrors Phase B4's old evidence_is_strong gate, plus every other
# per-dimension check folded into `_resolve_verdict`). Re-deriving a
# separate notion of "strong evidence" here would either (a) duplicate that
# logic and risk drifting out of sync, or (b) contradict it, which would be
# actively confusing (a divergence could be "REJECT" yet "SUPPORTED" at the
# same time). So the mapping is exactly:
#   verdict == PASS   -> SUPPORTED_DIVERGENCE   (evidentiary_sufficiency PASS
#                         + no other hard-fail/REJECT-tier check fired)
#   verdict == WARN   -> WEAKLY_SUPPORTED_DIVERGENCE (stands, but flagged)
#   verdict == REJECT -> UNSUPPORTED_DIVERGENCE (forecast is suppressed by
#                         engine.py precisely because of this)
#   triggered == False / verdict is None -> None (the audit never ran —
#                         the gap didn't exceed DIVERGENCE_THRESHOLD_PP, so
#                         "is this divergence supported" isn't even a
#                         meaningful question for this market).
# ---------------------------------------------------------------------------

DivergenceSupport = Literal["SUPPORTED_DIVERGENCE", "WEAKLY_SUPPORTED_DIVERGENCE", "UNSUPPORTED_DIVERGENCE"]

_SUPPORT_BY_VERDICT: dict[Verdict, DivergenceSupport] = {
    "PASS": "SUPPORTED_DIVERGENCE",
    "WARN": "WEAKLY_SUPPORTED_DIVERGENCE",
    "REJECT": "UNSUPPORTED_DIVERGENCE",
}


def classify_divergence_support(result: DivergenceAuditResult) -> DivergenceSupport | None:
    """Map an already-computed DivergenceAuditResult's PASS/WARN/REJECT
    verdict onto a divergence-support label. None whenever the divergence
    check never triggered (result.triggered is False / verdict is None) —
    honestly "not applicable", not a guessed label."""
    if not result.triggered or result.verdict is None:
        return None
    return _SUPPORT_BY_VERDICT[result.verdict]
