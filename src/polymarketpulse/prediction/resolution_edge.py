"""Resolution Edge Engine — structured, auditable scoring of how precisely
a market's resolution rules are written. Deliberately heuristic and
keyword-based (no LLM): the goal is to flag markets where the wording
itself is a trap (vague deadlines, subjective terms, no named authority),
not to claim a definitive legal reading.

Data reality check: most providers only expose a short `resolution_source`
label (e.g. "polymarket"), not the full resolution-criteria prose. When no
detailed resolution text is available, this module still analyzes the
market *question* wording (which on Polymarket/Manifold usually embeds the
actual condition, e.g. "Will the ceasefire be signed by March 1?") and is
honest about running in that degraded mode via `detail`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .resolution_rules import parse_resolution_conditions

# Terms that make a resolution condition concrete/checkable (per spec).
_PRECISION_TERMS = (
    "announced", "signed", "enacted", "effective", "confirmed",
    "officially recognized", "remains in effect", "by ", "before ",
)
# Terms that make a condition subjective/ambiguous, undermining clarity.
_AMBIGUITY_TERMS = (
    "significant", "substantial", "expected", "likely", "approximately",
    "widely regarded", "generally considered", "roughly", "around",
)
_DATE_PATTERN = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolutionEdgeResult:
    yes_condition: str
    no_condition: str
    has_explicit_deadline: bool
    authority_source: str | None
    clarity_score: float  # 0..100
    ambiguity_score: float  # 0..100
    source_authority_score: float  # 0..100
    deadline_precision_score: float  # 0..100
    dispute_risk_score: float  # 0..100
    resolution_edge_score: float  # 0..100 composite (higher = clearer/safer)
    risk_level: str  # "niedrig" | "mittel" | "hoch"
    pitfalls: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict:
        return {
            "yes_condition": self.yes_condition,
            "no_condition": self.no_condition,
            "has_explicit_deadline": self.has_explicit_deadline,
            "authority_source": self.authority_source,
            "clarity_score": self.clarity_score,
            "ambiguity_score": self.ambiguity_score,
            "source_authority_score": self.source_authority_score,
            "deadline_precision_score": self.deadline_precision_score,
            "dispute_risk_score": self.dispute_risk_score,
            "resolution_edge_score": self.resolution_edge_score,
            "risk_level": self.risk_level,
            "pitfalls": list(self.pitfalls),
            "detail": self.detail,
        }


def _term_hits(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [t for t in terms if t in lowered]


def compute_resolution_edge(
    question: str, resolution_text: str | None, authority_source: str | None = None
) -> ResolutionEdgeResult:
    yes_terms, no_terms, _subject = parse_resolution_conditions(question, resolution_text)
    combined_text = f"{question} {resolution_text or ''}"

    precision_hits = _term_hits(combined_text, _PRECISION_TERMS)
    ambiguity_hits = _term_hits(combined_text, _AMBIGUITY_TERMS)
    has_explicit_deadline = bool(_DATE_PATTERN.search(combined_text))

    yes_condition = " ".join(yes_terms) if yes_terms else "(nicht explizit im Text gefunden — siehe Marktfrage)"
    no_condition = " ".join(no_terms) if no_terms else "(nicht explizit im Text gefunden — siehe Marktfrage)"

    clarity_score = min(100.0, 30.0 + len(precision_hits) * 15.0 + (20.0 if (yes_terms or no_terms) else 0.0))
    ambiguity_score = min(100.0, len(ambiguity_hits) * 25.0)
    source_authority_score = 80.0 if authority_source else 35.0
    deadline_precision_score = 85.0 if has_explicit_deadline else 30.0
    dispute_risk_score = min(100.0, ambiguity_score * 0.6 + (0.0 if authority_source else 25.0) + (0.0 if has_explicit_deadline else 20.0))

    resolution_edge_score = round(
        max(
            0.0,
            min(
                100.0,
                clarity_score * 0.35
                + source_authority_score * 0.2
                + deadline_precision_score * 0.25
                - ambiguity_score * 0.1
                - dispute_risk_score * 0.1,
            ),
        ),
        1,
    )

    if resolution_edge_score >= 65:
        risk_level = "niedrig"
    elif resolution_edge_score >= 40:
        risk_level = "mittel"
    else:
        risk_level = "hoch"

    pitfalls: list[str] = []
    if not has_explicit_deadline:
        pitfalls.append("Keine explizite Frist im Text erkennbar.")
    if not authority_source:
        pitfalls.append("Keine eindeutige zuständige Resolution-Quelle bekannt.")
    if ambiguity_hits:
        pitfalls.append(f"Subjektive Begriffe gefunden: {', '.join(ambiguity_hits)}.")
    if not yes_terms and not no_terms:
        pitfalls.append("Keine explizite 'resolves YES/NO if...'-Klausel im verfügbaren Text gefunden.")

    detail = (
        f"{len(precision_hits)} präzise(r) Begriff(e), {len(ambiguity_hits)} mehrdeutige(r) Begriff(e), "
        f"explizite Frist: {'ja' if has_explicit_deadline else 'nein'}, "
        f"benannte Quelle: {'ja' if authority_source else 'nein'}. "
        "Analyse basiert auf Marktfrage" + (" und Resolution-Text" if resolution_text else " (kein separater Resolution-Text verfügbar)") + "."
    )

    return ResolutionEdgeResult(
        yes_condition=yes_condition,
        no_condition=no_condition,
        has_explicit_deadline=has_explicit_deadline,
        authority_source=authority_source,
        clarity_score=round(clarity_score, 1),
        ambiguity_score=round(ambiguity_score, 1),
        source_authority_score=round(source_authority_score, 1),
        deadline_precision_score=round(deadline_precision_score, 1),
        dispute_risk_score=round(dispute_risk_score, 1),
        resolution_edge_score=resolution_edge_score,
        risk_level=risk_level,
        pitfalls=tuple(pitfalls),
        detail=detail,
    )
