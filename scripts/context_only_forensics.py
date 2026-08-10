"""Deterministic forensic dump for the seven CONTEXT_ONLY acceptance markets."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymarketpulse.ai import service as ai_service
from polymarketpulse.storage import Storage

TARGET_IDS = (
    "561996", "polymarket:2252244", "polymarket:2252245",
    "polymarket:3241070", "polymarket:3241073", "polymarket:3241078", "polymarket:3241080",
)

DQ_WEIGHTS = {
    "proposition_clarity": 0.13,
    "resolution_semantics_clarity": 0.08,
    "historical_coverage": 0.18,
    "evidence_relevance": 0.18,
    "source_quality_independence": 0.18,
    "structured_data_availability": 0.15,
    "freshness": 0.10,
    "provider_health": 0.10,
}
CONFIDENCE_WEIGHTS = {
    "effective_sample_size": 0.15,
    "uncertainty_width": 0.15,
    "evidence_relevance": 0.15,
    "source_quality_independence": 0.10,
    "freshness": 0.10,
    "model_agreement": 0.15,
    "proposition_clarity": 0.05,
    "specialized_model_reliability": 0.10,
    "specialized_model_confidence": 0.20,
    "resolution_semantics_clarity": 0.05,
    "legacy_signals": 0.10,
    "provider_health": 0.05,
}


def _dimensions(composite, weights: dict[str, float]) -> list[dict]:
    if composite is None:
        return []
    available_weight = sum(
        weights.get(d.name, 0.0)
        for d in composite.dimensions
        if d.available and d.normalized_score is not None
    )
    return [
        {
            **d.as_dict(),
            "configured_weight": weights.get(d.name, 0.0),
            "effective_weight": (
                round(weights.get(d.name, 0.0) / available_weight, 4)
                if d.available and d.normalized_score is not None and available_weight
                else 0.0
            ),
            "contribution": (
                round(d.normalized_score * weights.get(d.name, 0.0) / available_weight, 2)
                if d.available and d.normalized_score is not None and available_weight
                else 0.0
            ),
        }
        for d in composite.dimensions
    ]


def _market_row(storage: Storage, market_id: str) -> dict:
    return ai_service._load_market_row(storage, market_id) or {}


def _evidence_counts(result) -> dict:
    ev = result.independent_evidence
    if ev is None:
        return {"available": False, "yes": 0, "no": 0, "relations": {}}
    items = (*ev.evidence_for_yes, *ev.evidence_for_no)
    relations: dict[str, int] = {}
    for item in items:
        relations[item.relation_label] = relations.get(item.relation_label, 0) + 1
    return {
        "available": ev.available,
        "yes": len(ev.evidence_for_yes),
        "no": len(ev.evidence_for_no),
        "confirmation_count": ev.confirmation_count,
        "counter_evidence_count": ev.counter_evidence_count,
        "source_quality_score": ev.source_quality_score,
        "relations": relations,
    }


def _maturity_conditions(result) -> dict:
    direct = 0
    if result.independent_evidence is not None:
        direct = sum(
            item.relation_label in ("DIRECT_YES", "DIRECT_NO")
            for item in (
                *result.independent_evidence.evidence_for_yes,
                *result.independent_evidence.evidence_for_no,
            )
        )
    gaps = result.data_gaps
    dq = result.data_quality_composite.score if result.data_quality_composite else None
    verdict = result.divergence_audit.verdict if result.divergence_audit else None
    domain_available = any(
        s.available and s.name in {"macro", "quant", "politics", "geopolitics", "sports"}
        for s in result.submodel_estimates
    )
    world_variables = result.world_state.state_variables if result.world_state else ()
    return {
        "semantics_clear": bool(result.proposition and result.proposition.proposition_status == "CLEAR"),
        "resolution_understood": bool(result.resolution_semantics and result.resolution_semantics.confidence >= 0.7),
        "domain_model_available": domain_available,
        "structured_world_state_present": bool(world_variables),
        "direct_evidence_count": direct,
        "confidence_at_least_70": result.confidence_score >= 70,
        "data_quality_at_least_60": dq is not None and dq >= 60,
        "no_high_or_critical_gaps": bool(gaps is None or (gaps.high_gaps == 0 and gaps.critical_gaps == 0)),
        "divergence_not_rejected": verdict != "REJECT",
    }


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    storage = Storage(root / "data" / "polymarketpulse.db", auto_migrate=False)
    output = []
    for market_id in TARGET_IDS:
        result = ai_service.get_prediction(storage, market_id)
        market = _market_row(storage, market_id)
        proposition = result.proposition
        output.append({
            "market_id": market_id,
            "question": market.get("question"),
            "domain": proposition.domain if proposition else None,
            "event_type": proposition.event_type if proposition else None,
            "market_probability": result.market_yes_probability,
            "independent_probability": result.independent_probability,
            "forecast_status": result.forecast_status,
            "maturity": result.forecast_maturity,
            "confidence": result.confidence_score,
            "data_quality": result.data_quality_composite.score if result.data_quality_composite else None,
            "uncertainty": [result.uncertainty_lower, result.uncertainty_upper],
            "submodels": [
                {
                    "name": s.name,
                    "available": s.available,
                    "probability": s.estimated_yes_probability,
                    "weight": s.weight,
                    "detail": s.detail,
                }
                for s in result.submodel_estimates
            ],
            "contributions": [c.as_dict() for c in result.contribution_breakdown],
            "maturity_conditions": _maturity_conditions(result),
            "confidence_breakdown": _dimensions(result.confidence_composite, CONFIDENCE_WEIGHTS),
            "data_quality_breakdown": _dimensions(result.data_quality_composite, DQ_WEIGHTS),
            "data_gaps": result.data_gaps.as_dict() if result.data_gaps else None,
            "divergence": result.divergence_audit.as_dict() if result.divergence_audit else None,
            "evidence": _evidence_counts(result),
            "world_state": result.world_state.as_dict() if result.world_state else None,
            "resolution": result.resolution_semantics.as_dict() if result.resolution_semantics else None,
        })
    if args.summary:
        summary = [
            {
                "market_id": item["market_id"], "question": item["question"],
                "domain": item["domain"], "event_type": item["event_type"],
                "market_probability": item["market_probability"],
                "independent_probability": item["independent_probability"],
                "maturity": item["maturity"], "forecast_status": item["forecast_status"],
                "confidence": item["confidence"], "data_quality": item["data_quality"],
                "available_models": [s["name"] for s in item["submodels"] if s["available"]],
                "gaps": [g["category"] for g in item["data_gaps"]["gaps"]] if item["data_gaps"] else [],
                "world_state_sources": [v["source"] for v in (item["world_state"] or {}).get("state_variables", [])],
                "divergence": (item["divergence"] or {}).get("verdict"),
            }
            for item in output
        ]
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
