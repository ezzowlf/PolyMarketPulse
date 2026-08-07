"""Test Quant model specifically with a price-threshold market."""

import httpx
import json

API_BASE = "http://127.0.0.1:8000"

# Get a market that should trigger Quant model
r = httpx.get(f"{API_BASE}/opportunities?limit=100")
markets = r.json()

# Find a market with quant eligibility
quant_markets = []
for m in markets:
    pred = httpx.get(f"{API_BASE}/prediction/{m['market_id']}").json()
    quant_model = next((c for c in pred["contribution_breakdown"] if c["source"] == "quant"), None)
    if quant_model and quant_model.get("eligible"):
        quant_markets.append((m, pred, quant_model))

print("=" * 60)
print("QUANT MODEL TEST")
print("=" * 60)
print()

if quant_markets:
    m, pred, quant_model = quant_markets[0]
    print(f"Market: {m['question']}")
    print()
    print(f"Quant model eligible: {quant_model.get('eligible')}")
    print(f"Quant model available: {quant_model.get('available')}")
    print(f"Quant detail: {quant_model.get('detail')}")
    print()
    print(f"Independent probability: {pred.get('independent_probability')}")
    print(f"Final probability: {pred.get('estimated_yes_probability')}")
    print(f"Status: {pred.get('forecast_status')}")
    
    # Show quant-specific inputs if available
    print()
    print("Quant model details:")
    if pred.get("historical_comparables"):
        print(f"  Historical comparables: {len(pred['historical_comparables'])} cases")
    
    # Check divergence
    if pred.get("divergence_audit"):
        print(f"  Divergence: {pred['divergence_audit'].get('verdict')}")
else:
    print("No markets found with Quant model eligible")
    print()
    print("Checking all markets with quant in contribution_breakdown:")
    for m in markets[:10]:
        pred = httpx.get(f"{API_BASE}/prediction/{m['market_id']}").json()
        quant_model = next((c for c in pred["contribution_breakdown"] if c["source"] == "quant"), None)
        if quant_model:
            print(f"  {m['question'][:50]}... -> eligible={quant_model.get('eligible')}, available={quant_model.get('available')}")