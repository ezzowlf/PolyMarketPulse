import httpx
import json

# Get 20 active markets
r = httpx.get('http://127.0.0.1:8000/opportunities?limit=5')
markets = r.json()
print(f"Got {len(markets)} markets from opportunities endpoint")

# Print first market summary
if markets:
    m = markets[0]
    print(f"First market: {m['question'][:60]}...")
    print(f"  ID: {m['market_id']}")
    print(f"  Market Prob: {m['market_yes_probability']*100:.1f}%")
    print(f"  Independent Prob: {m['independent_probability']*100:.1f}%")
    print(f"  Final Prob: {m['estimated_yes_probability']*100:.1f}%")
    print(f"  Edge: {m['net_yes_edge']*100:.1f}%")
    print(f"  Confidence: {m['confidence_score']:.1f}")
    print(f"  Status: {m['forecast_status']}")