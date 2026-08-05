async function renderBacktestPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Backtest…</div>`;
  try {
    const [report, cost, evaluation] = await Promise.all([Api.backtest(), Api.costReport(30), Api.evaluation()]);

    container.innerHTML = `
      <div class="disclaimer">
        Zeitbasierter Walk-Forward-Backtest: Für jeden aufgelösten Markt werden ausschließlich zuvor aufgelöste
        Märkte zur Bildung der historischen Basisrate verwendet — kein Look-ahead-Bias. Simulierte Rendite ist
        keine reale Handelsperformance, es werden keine echten Orders ausgeführt.
      </div>
      <div class="panel">
        <h3>Backtest der Prognose-Engine</h3>
        <div class="widget-grid">
          ${widgetCard({ title: "Ausgewertete Fälle (Out-of-Sample)", value: report.n_evaluated })}
          ${widgetCard({ title: "Übersprungen (zu wenig Trainingsdaten)", value: report.n_skipped })}
          ${widgetCard({ title: "Brier Score", value: report.brier_score !== null ? fmtNum(report.brier_score, 4) : "nicht verfügbar" })}
          ${widgetCard({ title: "Log Loss", value: report.log_loss !== null ? fmtNum(report.log_loss, 4) : "nicht verfügbar" })}
          ${widgetCard({ title: "Simulierte kumulierte Rendite", value: fmtNum(report.cumulative_return, 3) })}
          ${widgetCard({ title: "Max. Drawdown", value: fmtNum(report.max_drawdown, 3) })}
        </div>
      </div>

      <div class="panel">
        <h3>Kalibrierung (vorhergesagt vs. beobachtet)</h3>
        ${
          report.calibration.length
            ? `<table><thead><tr><th>Bucket</th><th>n</th><th>Ø vorhergesagt YES</th><th>beobachtete YES-Quote</th></tr></thead><tbody>
              ${report.calibration.map((b) => `<tr><td>${b.bucket_predicted_range}</td><td>${b.n}</td><td>${fmtPct(b.avg_predicted_yes)}</td><td>${fmtPct(b.observed_yes_rate)}</td></tr>`).join("")}
              </tbody></table>`
            : `<div class="empty-state">Keine ausreichenden Daten für eine Kalibrierungsauswertung.</div>`
        }
      </div>

      <div class="panel">
        <h3>Performance nach Richtung</h3>
        <div class="widget-grid">
          ${widgetCard({ title: "YES-Empfehlungen: Trades", value: report.performance_yes.n_trades ?? 0, sub: `Rendite ${fmtNum(report.performance_yes.cumulative_return, 3)}, Drawdown ${fmtNum(report.performance_yes.max_drawdown, 3)}` })}
          ${widgetCard({ title: "NO-Empfehlungen: Trades", value: report.performance_no.n_trades ?? 0, sub: `Rendite ${fmtNum(report.performance_no.cumulative_return, 3)}, Drawdown ${fmtNum(report.performance_no.max_drawdown, 3)}` })}
        </div>
      </div>

      <div class="panel">
        <h3>Performance nach Kategorie</h3>
        ${
          Object.keys(report.performance_by_category).length
            ? `<table><thead><tr><th>Kategorie</th><th>n</th><th>Brier</th><th>YES-Rendite</th><th>NO-Rendite</th></tr></thead><tbody>
              ${Object.entries(report.performance_by_category)
                .map(([cat, s]) => `<tr><td>${cat}</td><td>${s.n_evaluated}</td><td>${fmtNum(s.brier_score, 4)}</td><td>${fmtNum(s.yes.cumulative_return, 3)}</td><td>${fmtNum(s.no.cumulative_return, 3)}</td></tr>`)
                .join("")}
              </tbody></table>`
            : `<div class="empty-state">Keine Kategoriedaten.</div>`
        }
      </div>

      <div class="panel">
        <h3>Reale Prognose-Historie (Performance Tracking)</h3>
        <div class="disclaimer">
          Jede berechnete Prognose wird dauerhaft gespeichert und nach Marktauflösung automatisch ausgewertet —
          im Unterschied zum Backtest oben ist dies der tatsächliche, im Betrieb erzielte Track-Record.
        </div>
        <div class="widget-grid">
          ${widgetCard({ title: "Gespeicherte Prognosen", value: evaluation.n_snapshots_total })}
          ${widgetCard({ title: "Bereits aufgelöst", value: evaluation.n_evaluable })}
          ${widgetCard({ title: "Mit YES/NO-Empfehlung", value: evaluation.n_directional })}
          ${widgetCard({ title: "Accuracy", value: evaluation.accuracy !== null ? fmtPct(evaluation.accuracy) : "–" })}
          ${widgetCard({ title: "Precision", value: evaluation.precision !== null ? fmtPct(evaluation.precision) : "–" })}
          ${widgetCard({ title: "Recall", value: evaluation.recall !== null ? fmtPct(evaluation.recall) : "–" })}
          ${widgetCard({ title: "Brier Score", value: evaluation.brier_score !== null ? fmtNum(evaluation.brier_score, 4) : "–" })}
          ${widgetCard({ title: "Log Loss", value: evaluation.log_loss !== null ? fmtNum(evaluation.log_loss, 4) : "–" })}
          ${widgetCard({ title: "Ø Netto-Edge", value: evaluation.average_net_edge !== null ? (evaluation.average_net_edge * 100).toFixed(1) + " pp" : "–" })}
          ${widgetCard({ title: "Simulierter ROI", value: evaluation.simulated_roi !== null ? fmtPct(evaluation.simulated_roi) : "–" })}
        </div>
      </div>

      <div class="panel">
        <h3>KI-Kostenbericht (letzte 30 Tage)</h3>
        <div class="widget-grid">
          ${widgetCard({ title: "Heute ausgegeben", value: "$" + fmtNum(cost.spent_today_usd, 6) })}
          ${widgetCard({ title: "Regelbasierte Fallback-Analysen", value: cost.rule_based_fallback_runs })}
        </div>
        ${
          cost.by_model.length
            ? `<table><thead><tr><th>Modell</th><th>Analysen</th><th>Live</th><th>Aus Cache</th><th>Gesamtkosten</th><th>Ø Kosten</th></tr></thead><tbody>
              ${cost.by_model.map((r) => `<tr><td>${r.model}</td><td>${r.runs}</td><td>${r.live_runs}</td><td>${r.cache_hits}</td><td>$${fmtNum(r.total_actual_cost_usd, 5)}</td><td>$${fmtNum(r.avg_actual_cost_usd, 6)}</td></tr>`).join("")}
              </tbody></table>`
            : `<div class="empty-state">Noch keine KI-Analysen im Zeitraum.</div>`
        }
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}
