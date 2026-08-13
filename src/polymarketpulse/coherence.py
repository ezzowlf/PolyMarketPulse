"""Provenance-only coherence checks; no automatic probability mutation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CoherenceWarning:
    relationship_id: int
    status: str
    severity: str
    explanation: str

def audit_relationship(relation_type: str, probability_a: float | None, probability_b: float | None, relationship_id: int = 0) -> CoherenceWarning:
    if probability_a is None or probability_b is None:
        return CoherenceWarning(relationship_id, "UNKNOWN", "INFO", "Keine beiden aktuellen Marktpreise für einen Coherence-Check verfügbar.")
    violation = (relation_type in {"PARENT_CHILD", "CONDITIONAL"} and probability_b > probability_a) or (relation_type == "MUTUALLY_EXCLUSIVE" and probability_a + probability_b > 1.0001) or (relation_type == "COMPLEMENT" and abs((probability_a + probability_b) - 1) > .08)
    if not violation:
        return CoherenceWarning(relationship_id, "CONSISTENT", "INFO", "Explizite Marktbeziehung ist mit den aktuellen Preisen konsistent.")
    return CoherenceWarning(relationship_id, "COHERENCE_WARNING", "WARNING", f"{relation_type}: Werte {probability_a:.1%} und {probability_b:.1%} sind logisch nicht konsistent; gezielte Recherche erforderlich.")
