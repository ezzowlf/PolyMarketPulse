async function renderAnalyticsPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Analytics…</div>`;
  const [analytics, markets, resolutions] = await Promise.all([
    Api.analytics(),
    Api.markets({ limit: 200 }),
    Api.resolutions(),
  ]);
  const stats = analytics.signal_stats;

  const scoreBuckets = { "0-25": 0, "25-50": 0, "50-75": 0, "75-100": 0 };
  for (const m of markets.items) {
    const s = m.opportunity_score;
    if (s === null || s === undefined) continue;
    if (s < 25) scoreBuckets["0-25"]++;
    else if (s < 50) scoreBuckets["25-50"]++;
    else if (s < 75) scoreBuckets["50-75"]++;
    else scoreBuckets["75-100"]++;
  }

  const categoryCounts = {};
  for (const m of markets.items) {
    const cat = m.category || "unbekannt";
    categoryCounts[cat] = (categoryCounts[cat] || 0) + 1;
  }

  const durations = resolutions
    .filter((r) => r.resolved_at && r.detected_at)
    .map((r) => (new Date(r.detected_at) - new Date(r.resolved_at)) / 3600000)
    .filter((h) => Number.isFinite(h) && h >= 0);
  const avgResolutionLagHours = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

  container.innerHTML = `
    <div class="widget-grid">
      ${widgetCard({ title: "Trefferquote", value: stats.hit_rate !== null ? fmtPct(stats.hit_rate) : "–" })}
      ${widgetCard({ title: "Virtuelle Rendite (Ø)", value: stats.average_simulated_return !== null ? fmtNum(stats.average_simulated_return, 3) : "–" })}
      ${widgetCard({ title: "Brier Score", value: stats.brier_score !== null ? fmtNum(stats.brier_score, 3) : "nicht verfügbar" })}
      ${widgetCard({ title: "Log Loss", value: stats.log_loss !== null ? fmtNum(stats.log_loss, 3) : "nicht verfügbar" })}
      ${widgetCard({ title: "Ø Resolution-Erkennungsverzug (h)", value: avgResolutionLagHours !== null ? fmtNum(avgResolutionLagHours, 1) : "–" })}
      ${widgetCard({ title: "Märkte (Stichprobe)", value: markets.total })}
    </div>
    <div class="panel">
      <h3>Research-Score-Verteilung</h3>
      <table><thead><tr><th>Bereich</th><th>Anzahl</th></tr></thead><tbody>
        ${Object.entries(scoreBuckets).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}
      </tbody></table>
    </div>
    <div class="panel">
      <h3>Märkte nach Kategorie</h3>
      <table><thead><tr><th>Kategorie</th><th>Anzahl</th></tr></thead><tbody>
        ${Object.entries(categoryCounts).sort((a, b) => b[1] - a[1]).slice(0, 20).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}
      </tbody></table>
    </div>
    <div class="panel">
      <h3>Nach Signaltyp</h3>
      ${renderBreakdownTable(stats.breakdown_by_type)}
    </div>
    <div class="panel">
      <h3>Nach Provider</h3>
      ${renderBreakdownTable(stats.breakdown_by_provider)}
    </div>
  `;
}
