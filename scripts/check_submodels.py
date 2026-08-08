"""Check submodel estimates and weights."""

import httpx

API_BASE = "http://127.0.0.1:8000"

# Test J.D. Vance market with politics
r = httpx.get(f"{API_BASE}/prediction/polymarket:561974")
p = r.json()

print("=" * 80)
print("J.D. VANCE MARKET - SUBMODEL ESTIMATES")
print("=" * 80)
print()

for s in p.get("submodel_estimates", []):
    print(f"{s['name']}:")
    print(f"  estimated_yes_probability: {s['estimated_yes_probability']}")
    print(f"  weight: {s['weight']}")
    print(f"  available: {s['available']}")
    print()

print(f"Forecast status: {p.get('forecast_status')}")
print(f"Independent probability: {p.get('independent_probability')}")
print(f"Final probability: {p.get('estimated_yes_probability')}")