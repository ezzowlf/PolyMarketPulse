"""Research Queue — ranks real markets by how valuable it would be to
research them next, so the Live Evidence Engine doesn't blindly crawl all
305 markets in an arbitrary order.

Priority is a pure function of REAL, already-computed signals:

- divergence: |model_hypothesis_probability - market_probability|, only
  when a real model_hypothesis exists (no hypothesis = nothing to verify).
- deadline_pressure: markets resolving soon are more urgent to research
  than markets months away.
- critical_gap_count / high_gap_count: from data_gaps.calculate_data_gaps
  — more unresolved *critical* gaps means research would move the needle.
- source_coverage: whether source_registry actually has real sources for
  this market's category/event_type at all. A category with zero real
  source coverage is NOT researchable right now — deprioritized, not
  penalized to zero (source coverage may improve later).

No LLM calls, no network calls inside this module — it operates on
already-computed/cheap-to-compute inputs, so it can rank all real markets
fast before deciding which few actually get a real (network-bound) research
attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MarketSignal:
    """Real, already-computed inputs for one market's priority score."""

    market_id: str
    question: str
    category: str | None
    event_type: str | None
    market_probability: float | None
    model_hypothesis_probability: float | None
    time_remaining_hours: float | None
    critical_gap_count: int
    high_gap_count: int
    has_source_coverage: bool
    early_signal_count: int = 0


@dataclass(frozen=True)
class QueueEntry:
    """One ranked research candidate."""

    market_id: str
    question: str
    priority_score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _divergence_score(signal: MarketSignal) -> tuple[float, str | None]:
    if signal.model_hypothesis_probability is None or signal.market_probability is None:
        return 0.0, None
    gap = abs(signal.model_hypothesis_probability - signal.market_probability)
    if gap < 0.05:
        return 0.0, None
    # 0..1 gap maps to 0..40 points — a real, unverified divergence is the
    # single strongest research signal (per the project's own priority:
    # "Ist diese mögliche Edge echt?").
    return round(gap * 40.0, 2), f"Ungeprüfte Divergenz {gap * 100:.1f}pp (Modell vs. Markt)"


def _deadline_score(signal: MarketSignal) -> tuple[float, str | None]:
    if signal.time_remaining_hours is None:
        return 0.0, None
    if signal.time_remaining_hours <= 0:
        return 0.0, None
    if signal.time_remaining_hours <= 72:
        return 20.0, f"Deadline in {signal.time_remaining_hours:.0f}h — dringend"
    if signal.time_remaining_hours <= 24 * 14:
        return 10.0, f"Deadline in {signal.time_remaining_hours / 24:.0f} Tagen"
    return 2.0, None


def _gap_score(signal: MarketSignal) -> tuple[float, str | None]:
    score = signal.critical_gap_count * 8.0 + signal.high_gap_count * 3.0
    if score <= 0:
        return 0.0, None
    reason = f"{signal.critical_gap_count} kritische / {signal.high_gap_count} wichtige Data Gaps offen"
    return min(score, 30.0), reason


def compute_priority(signal: MarketSignal) -> QueueEntry:
    """Real, deterministic priority score. Higher = more valuable to research now."""
    reasons: list[str] = []
    score = 0.0

    div_score, div_reason = _divergence_score(signal)
    score += div_score
    if div_reason:
        reasons.append(div_reason)

    dl_score, dl_reason = _deadline_score(signal)
    score += dl_score
    if dl_reason:
        reasons.append(dl_reason)

    gap_score, gap_reason = _gap_score(signal)
    score += gap_score
    if gap_reason:
        reasons.append(gap_reason)
    if signal.early_signal_count:
        score += min(signal.early_signal_count * 6.0, 18.0)
        reasons.append(f"{signal.early_signal_count} unbestätigte Frühwarnsignal(e) — Verifikation ausstehend")

    if not signal.has_source_coverage:
        # Deprioritized, not zeroed: real source coverage may improve
        # later (e.g. a new registry entry), and the market itself may
        # still be worth a generic-news attempt.
        score *= 0.3
        reasons.append("keine registrierten Primärquellen für diese Kategorie — Recherche derzeit wenig aussichtsreich")
    else:
        reasons.append("reale Quellenabdeckung vorhanden")

    return QueueEntry(
        market_id=signal.market_id,
        question=signal.question,
        priority_score=round(score, 2),
        reasons=tuple(reasons),
    )


def build_research_queue(signals: list[MarketSignal], limit: int | None = None) -> list[QueueEntry]:
    """Rank real markets by real priority signals, highest first."""
    entries = [compute_priority(s) for s in signals]
    entries.sort(key=lambda e: e.priority_score, reverse=True)
    return entries[:limit] if limit is not None else entries
