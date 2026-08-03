async function renderQualityPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Data-Quality-Reports…</div>`;
  const reports = await Api.quality();
  if (reports.length === 0) {
    container.innerHTML = `<div class="empty-state">Noch keine Data-Quality-Reports. Führe zuerst \`polymarketpulse scan\` aus.</div>`;
    return;
  }
  const avg = reports.reduce((sum, r) => sum + r.score, 0) / reports.length;
  container.innerHTML = `
    <div class="disclaimer">Der Data-Quality-Score bewertet Vollständigkeit und Plausibilität der gespeicherten Rohdaten je Markt — keine Aussage zur Markterwartung.</div>
    <div class="widget-grid">
      ${widgetCard({ title: "Ø Datenqualität", value: fmtNum(avg, 1) + "%" })}
      ${widgetCard({ title: "Geprüfte Märkte", value: reports.length })}
      ${widgetCard({ title: "Märkte mit Problemen", value: reports.filter((r) => r.issues.length > 0).length })}
    </div>
    <div class="panel">
      ${reports
        .map(
          (r) => `
        <div style="padding:10px 0;border-bottom:1px solid var(--border)">
          <div style="display:flex;justify-content:space-between">
            <span>${r.question || r.provider_market_id} <span class="badge">${r.provider}</span></span>
            <span class="badge ${r.score >= 90 ? "green" : r.score >= 70 ? "yellow" : "red"}">${r.score.toFixed(0)}%</span>
          </div>
          ${r.checks_passed.length ? `<div class="sub">✓ ${r.checks_passed.join(" · ")}</div>` : ""}
          ${r.issues.length ? `<div class="sub" style="color:var(--red)">✗ ${r.issues.join(" · ")}</div>` : ""}
        </div>`
        )
        .join("")}
    </div>
  `;
}
