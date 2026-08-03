async function renderStatsPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Statistik…</div>`;
  const s = await Api.stats();
  container.innerHTML = `
    <div class="disclaimer">Brier Score und Log Loss werden nur berechnet, wenn ein echtes forecast_probability-Feld vorliegt — der Research-Score selbst gilt nicht als Wahrscheinlichkeit.</div>
    <div class="widget-grid">
      ${widgetCard({ title: "Signale gesamt", value: fmtNum(s.signal_count) })}
      ${widgetCard({ title: "Ausgewertet", value: fmtNum(s.evaluated_count) })}
      ${widgetCard({ title: "Trefferquote", value: s.hit_rate !== null ? fmtPct(s.hit_rate) : "–" })}
      ${widgetCard({ title: "Ø Signalpreis", value: s.average_signal_price !== null ? fmtPct(s.average_signal_price) : "–" })}
      ${widgetCard({ title: "Ø simulierte Rendite (1 virt. Einheit)", value: s.average_simulated_return !== null ? fmtNum(s.average_simulated_return, 3) : "–" })}
      ${widgetCard({ title: "Brier Score", value: s.brier_score !== null ? fmtNum(s.brier_score, 3) : "nicht verfügbar" })}
      ${widgetCard({ title: "Log Loss", value: s.log_loss !== null ? fmtNum(s.log_loss, 3) : "nicht verfügbar" })}
    </div>
    <div class="panel">
      <h3>Nach Signaltyp</h3>
      ${renderBreakdownTable(s.breakdown_by_type)}
    </div>
    <div class="panel">
      <h3>Nach Provider</h3>
      ${renderBreakdownTable(s.breakdown_by_provider)}
    </div>
    <div class="panel">
      <h3>Nach Kategorie</h3>
      ${renderBreakdownTable(s.breakdown_by_category)}
    </div>
    <div class="panel">
      <h3>Nach Liquidität</h3>
      ${renderBreakdownTable(s.breakdown_by_liquidity)}
    </div>
    <div class="panel">
      <h3>Nach Restlaufzeit</h3>
      ${renderBreakdownTable(s.breakdown_by_time_to_resolution)}
    </div>
  `;
}
