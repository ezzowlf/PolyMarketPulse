async function renderPerformancePage(container) {
  container.innerHTML = `<div class="empty-state">Lade Performance…</div>`;
  const [perf, sim] = await Promise.all([Api.performance(), Api.simulation(50)]);

  container.innerHTML = `
    <div class="disclaimer">Simulation mit 1.0 virtueller Einheit je Signal. Kein Echtgeld, keine Handlungsaufforderung.</div>
    <div class="widget-grid">
      ${widgetCard({ title: "Ausgewertete Signale", value: fmtNum(perf.evaluated_count) })}
      ${widgetCard({ title: "Kumulierte Rendite (virt.)", value: perf.cumulative_return !== null ? fmtNum(perf.cumulative_return, 3) : "–" })}
      ${widgetCard({ title: "Ø Rendite je Signal", value: perf.average_return_per_signal !== null ? fmtNum(perf.average_return_per_signal, 3) : "–" })}
      ${widgetCard({ title: "Max. Drawdown", value: perf.max_drawdown !== null ? fmtNum(perf.max_drawdown, 3) : "–" })}
      ${widgetCard({ title: "Trefferquote", value: perf.win_rate !== null ? fmtPct(perf.win_rate) : "–" })}
      ${widgetCard({ title: "Ø Haltedauer (h)", value: perf.average_hold_hours !== null ? fmtNum(perf.average_hold_hours, 1) : "–" })}
    </div>
    <div class="panel">
      <h3>Equity-Kurve (virtuell)</h3>
      <canvas class="chart-canvas" id="equity-chart"></canvas>
    </div>
    <div class="panel">
      <h3>Signal-Simulationen</h3>
      ${
        sim.length
          ? `<table><thead><tr><th>Markt</th><th>Typ</th><th>Startpreis</th><th>Ergebnis</th><th>Korrekt</th><th>Rendite</th><th>Dauer (h)</th></tr></thead><tbody>
        ${sim
          .map(
            (s) => `<tr><td>${s.question || s.provider_market_id}</td><td>${s.signal_type}</td>
              <td>${s.origin_yes_price !== null ? fmtPct(s.origin_yes_price) : "–"}</td>
              <td>${s.final_outcome || "–"}</td>
              <td>${s.correct === null ? "n/a" : s.correct ? "✓" : "✗"}</td>
              <td>${s.simulated_pnl_per_unit !== null ? fmtNum(s.simulated_pnl_per_unit, 3) : "–"}</td>
              <td>${s.hold_duration_hours !== null ? fmtNum(s.hold_duration_hours, 1) : "–"}</td></tr>`
          )
          .join("")}
        </tbody></table>`
          : `<div class="empty-state">Noch keine ausgewerteten Simulationen.</div>`
      }
    </div>
  `;

  if (perf.equity_curve && perf.equity_curve.length) {
    renderLineChart(
      document.getElementById("equity-chart"),
      perf.equity_curve.map((p) => ({ y: p.equity, label: p.evaluated_at })),
      { color: "#2fd67f", decimals: 3 }
    );
  }
}
