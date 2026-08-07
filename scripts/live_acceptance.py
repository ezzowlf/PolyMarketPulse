"""Live Acceptance Test — run 20 real Polymarket markets through the API."""

import json
from pathlib import Path

import httpx

API_BASE = "http://127.0.0.1:8000"


def get_markets(limit: int = 20, min_confidence: int = 10) -> list[dict]:
    """Get markets from opportunities endpoint."""
    r = httpx.get(f"{API_BASE}/opportunities", params={
        "limit": limit,
        "min_confidence": min_confidence,
    })
    r.raise_for_status()
    return r.json()


def get_prediction(market_id: str) -> dict:
    """Get full prediction for a market."""
    r = httpx.get(f"{API_BASE}/prediction/{market_id}")
    r.raise_for_status()
    return r.json()


def format_prob(p: float | None) -> str:
    if p is None:
        return "—"
    return f"{p*100:.1f}%"


def main():
    print("=" * 80)
    print("LIVE ACCEPTANCE TEST — 20 REAL POLYMARKET MARKETS")
    print("=" * 80)
    print()
    
    # Get markets
    markets = get_markets(limit=30, min_confidence=10)
    print(f"Got {len(markets)} markets from opportunities endpoint")
    print()
    
    results = []
    
    for m in markets[:20]:
        market_id = m["market_id"]
        question = m["question"]
        
        print(f"Market: {question[:60]}...")
        print(f"  ID: {market_id}")
        print(f"  Category: {m.get('category', 'N/A')}")
        
        try:
            pred = get_prediction(market_id)
            
            # Extract key fields
            market_prob = pred.get("market_yes_probability")
            independent_prob = pred.get("independent_probability")
            final_prob = pred.get("estimated_yes_probability")
            blended_prob = pred.get("blended_probability")
            net_edge = pred.get("net_yes_edge")
            confidence = pred.get("confidence_score")
            data_quality = pred.get("data_quality_score")
            status = pred.get("forecast_status")
            recommendation = pred.get("recommendation")
            
            models_used = [
                c["source"] for c in pred.get("contribution_breakdown", [])
                if c["available"] and c["estimated_yes_probability"] is not None
            ]
            
            divergence = None
            if pred.get("divergence_audit"):
                divergence = pred["divergence_audit"].get("verdict")
            
            print(f"  Market Probability: {format_prob(market_prob)}")
            print(f"  Independent Probability: {format_prob(independent_prob)}")
            print(f"  Final Probability: {format_prob(final_prob)}")
            print(f"  Edge: {format_prob(net_edge)}")
            print(f"  Confidence: {confidence}")
            print(f"  Data Quality: {data_quality}")
            print(f"  Forecast Status: {status}")
            print(f"  Recommendation: {recommendation}")
            print(f"  Models used: {', '.join(models_used) if models_used else 'none'}")
            if divergence:
                print(f"  Divergence: {divergence}")
            
            results.append({
                "question": question,
                "market_id": market_id,
                "category": m.get("category"),
                "market_prob": market_prob,
                "independent_prob": independent_prob,
                "blended_prob": blended_prob,
                "final_prob": final_prob,
                "edge": net_edge,
                "confidence": confidence,
                "data_quality": data_quality,
                "status": status,
                "recommendation": recommendation,
                "models_used": models_used,
                "divergence": divergence,
            })
            
        except httpx.HTTPError as e:
            print(f"  ERROR: {e}")
            results.append({
                "question": question,
                "market_id": market_id,
                "category": m.get("category"),
                "error": str(e),
            })
        
        print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    usable = [r for r in results if "error" not in r and r.get("independent_prob") is not None]
    no_forecast = [r for r in results if "error" not in r and r.get("independent_prob") is None]
    errors = [r for r in results if "error" in r]
    
    print(f"Total markets: {len(results)}")
    print(f"Usable forecasts (independent_prob available): {len(usable)}")
    print(f"NO_FORECAST (insufficient data): {len(no_forecast)}")
    print(f"Errors: {len(errors)}")
    
    if usable:
        avg_conf = sum(r["confidence"] for r in usable if r["confidence"]) / len(usable)
        avg_dq = sum(r["data_quality"] for r in usable if r["data_quality"]) / len(usable)
        print(f"Avg Confidence: {avg_conf:.1f}")
        print(f"Avg Data Quality: {avg_dq:.1f}")
    
    # Save detailed results
    output_path = Path("acceptance_results.json")
    output_path.write_text(json.dumps(results, indent=2, default=str))
    print()
    print(f"Detailed results saved to {output_path}")
    
    # Also save simple summary
    simple = [
        {
            "question": r.get("question", "ERROR"),
            "id": r.get("market_id", "ERROR"),
            "status": r.get("status", "ERROR"),
            "independent_prob": r.get("independent_prob"),
            "final_prob": r.get("final_prob"),
            "edge": r.get("edge"),
            "confidence": r.get("confidence"),
        }
        for r in results
    ]
    simple_path = Path("acceptance_summary.json")
    simple_path.write_text(json.dumps(simple, indent=2, default=str))
    print(f"Simple summary saved to {simple_path}")


if __name__ == "__main__":
    main()