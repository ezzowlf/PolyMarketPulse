"""Test Trump/Nevada regression protection specifically."""


import httpx

API_BASE = "http://127.0.0.1:8000"

# Test 1: Trump/Nevada should be available (not blocked by protection)
# The protection only blocks if location="Nevada" AND subject contains "trump"
# Current market is "Trump out as President by August 31?" - no Nevada location

r = httpx.get(f"{API_BASE}/prediction/polymarket:3231771")
p = r.json()

print("=" * 60)
print("TRUMP/Nevada PROTECTION TEST")
print("=" * 60)
print()
print("Market: Trump out as President by August 31?")
print()

# Check if Politics model was eligible
politics_model = next((c for c in p["contribution_breakdown"] if c["source"] == "politics"), None)

if politics_model:
    print(f"Politics model eligible: {politics_model.get('eligible')}")
    print(f"Politics model available: {politics_model.get('available')}")
    print(f"Politics detail: {politics_model.get('detail')}")
    
    # Check if it's blocked by Trump/Nevada
    if "protected" in politics_model.get("detail", "").lower():
        print("❌ FAILURE: Politics model blocked by Trump/Nevada protection")
    else:
        print("✅ Politics model ran (not blocked by protection)")
else:
    print("❌ Politics model not found in contribution_breakdown")

print()
print("Full contribution breakdown:")
for c in p["contribution_breakdown"]:
    print(f"  {c['source']}: available={c['available']}, prob={c['estimated_yes_probability']}, eligible={c.get('eligible')}")

print()
print("Divergence audit:")
if p.get("divergence_audit"):
    print(f"  Verdict: {p['divergence_audit'].get('verdict')}")
    print(f"  Gap: {p['divergence_audit'].get('gap')}")
    print(f"  Summary: {p['divergence_audit'].get('summary')}")
else:
    print("  None")

print()
print("Probability values:")
print(f"  Market: {p.get('market_yes_probability')}")
print(f"  Independent: {p.get('independent_probability')}")
print(f"  Final: {p.get('estimated_yes_probability')}")
print(f"  Status: {p.get('forecast_status')}")