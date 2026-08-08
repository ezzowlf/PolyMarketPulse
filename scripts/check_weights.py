"""Check exact weight values."""

import httpx

API_BASE = "http://127.0.0.1:8000"

r = httpx.get(f"{API_BASE}/prediction/polymarket:561974")
p = r.json()

print("=" * 80)
print("EXACT WEIGHT VALUES")
print("=" * 80)
print()

for s in p.get("submodel_estimates", []):
    if s['name'] == "politics":
        print("Politics:")
        print(f"  weight = {s['weight']!r}")
        print(f"  type(weight) = {type(s['weight'])}")
        print(f"  weight > 0 = {s['weight'] > 0 if s['weight'] is not None else 'N/A'}")
        print(f"  weight == 0.0 = {s['weight'] == 0.0 if s['weight'] is not None else 'N/A'}")
        print()

for c in p.get("contribution_breakdown", []):
    if c['source'] == "politics":
        print("Politics (contribution_breakdown):")
        print(f"  weight_share = {c['weight_share']!r}")
        print()