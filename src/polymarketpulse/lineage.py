"""Audits the real claim/market/source chain without fabricating links."""
from __future__ import annotations


def audit_market_lineage(conn, market_id: str) -> dict:
    row = conn.execute("SELECT provider,provider_market_id FROM markets WHERE market_id=?", (market_id,)).fetchone()
    if not row: return {"status":"BROKEN","severity":"CRITICAL","issues":["MARKET_MISSING"]}
    claims = conn.execute("SELECT c.claim_id,c.source_id,c.source_url FROM claim_market_links l JOIN claims c ON c.claim_id=l.claim_id WHERE l.provider=? AND l.provider_market_id=?", row).fetchall()
    issues = [{"claim_id":cid,"issue":"CLAIM_SOURCE_MISSING"} for cid,sid,url in claims if not sid or not url]
    snapshot = conn.execute("SELECT id,published_forecast_probability FROM prediction_snapshots WHERE market_id=? ORDER BY created_at DESC LIMIT 1", (market_id,)).fetchone()
    if snapshot and snapshot[1] is not None and not claims: issues.append({"issue":"PUBLISHED_FORECAST_WITHOUT_LINKED_CLAIM"})
    status = "HEALTHY" if not issues else "BROKEN" if any(i.get("issue") == "PUBLISHED_FORECAST_WITHOUT_LINKED_CLAIM" for i in issues) else "WARNING"
    return {"status":status,"severity":"CRITICAL" if status == "BROKEN" else "WARNING" if issues else "INFO","snapshot_id":snapshot[0] if snapshot else None,"claim_count":len(claims),"issues":issues}
