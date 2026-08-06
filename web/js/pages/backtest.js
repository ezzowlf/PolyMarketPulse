const MIN_SAMPLE_FOR_SIGNIFICANCE = 20;

function _notSignificantNote(n) {
  return n < MIN_SAMPLE_FOR_SIGNIFICANCE
    ? `<p class="sub">Noch nicht statistisch aussagekräftig (${n} von mindestens ${MIN_SAMPLE_FOR_SIGNIFICANCE} Fällen).</p>`
    : "";
}

async function renderBacktestPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Auswertung…</div>`;
  try {
    const [report, cost, evaluation] = await Promise.all([Api.backtest(), Api.costReport(30), Api.evaluation()]);

    container.innerHTML = `
      <div class="disclaimer">
        Track Record der eigenen Prognose-Engine. Simulierte Werte sind keine reale Handelsperformance — es
        werden keine echten Orders ausgeführt.
      </div>

      <div class="panel">
        <h3>Bisherige Prognosen</h3>
        <div class="widget-grid">
          ${widgetCard({ title: "Prognosen insgesamt", value: evaluation.n_snapshots_total })}
          ${widgetCard({ title: "Abgeschlossene Märkte", value: evaluation.n_evaluable })}
          ${widgetCard({ title: "Trefferquote", value: evaluation.accuracy !== null ? fmtPct(evaluation.accuracy) : "–" })}
          ${widgetCard({ title: "Brier Score", value: evaluation.brier_score !== null ? fmtNum(evaluation.brier_score, 4) : "–" })}
          ${widgetCard({ title: "Ø Netto-Edge", value: evaluation.average_net_edge !== null ? (evaluation.average_net_edge * 100).toFixed(1) + " pp" : "–" })}
          ${widgetCard({ title: "Simuliertes Ergebnis (ROI)", value: evaluation.simulated_roi !== null ? fmtPct(evaluation.simulated_roi) : "–" })}
        </div>
        ${_notSignificantNote(evaluation.n_directional)}
      </div>

      <div class="panel">
        <h3>Kalibrierung <span class="sub">(vorhergesagt vs. tatsächlich eingetreten)</span></h3>
        ${
          report.calibration.length
            ? `<table><thead><tr><th>Bucket</th><th>n</th><th>Ø vorhergesagt YES</th><th>beobachtete YES-Quote</th></tr></thead><tbody>
              ${report.calibration.map((b) => `<tr><td>${b.bucket_predicted_range}</td><td>${b.n}</td><td>${fmtPct(b.avg_predicted_yes)}</td><td>${fmtPct(b.observed_yes_rate)}</td></tr>`).join("")}
              </tbody></table>`
            : `<div class="empty-state">Noch nicht statistisch aussagekräftig — zu wenige aufgelöste Fälle.</div>`
        }
      </div>

      <div class="panel">
        <h3>Nach Kategorie</h3>
        ${
          Object.keys(report.performance_by_category).length
            ? `<table><thead><tr><th>Kategorie</th><th>n</th><th>Brier</th><th>YES-Rendite</th><th>NO-Rendite</th></tr></thead><tbody>
              ${Object.entries(report.performance_by_category)
                .map(([cat, s]) => `<tr><td>${cat}</td><td>${s.n_evaluated}</td><td>${fmtNum(s.brier_score, 4)}</td><td>${fmtNum(s.yes.cumulative_return, 3)}</td><td>${fmtNum(s.no.cumulative_return, 3)}</td></tr>`)
                .join("")}
              </tbody></table>`
            : `<div class="empty-state">Noch nicht statistisch aussagekräftig — zu wenige aufgelöste Fälle je Kategorie.</div>`
        }
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

      <details class="panel">
        <summary><h3 style="display:inline">Erweitert: Backtest &amp; technische Metriken</h3></summary>
        <p class="disclaimer">
          Zeitbasierter Walk-Forward-Backtest: Für jeden aufgelösten Markt werden ausschließlich zuvor aufgelöste
          Märkte zur Bildung der historischen Basisrate verwendet — kein Look-ahead-Bias.
        </p>
        <div class="widget-grid">
          ${widgetCard({ title: "Ausgewertete Fälle (Out-of-Sample)", value: report.n_evaluated })}
          ${widgetCard({ title: "Übersprungen (zu wenig Trainingsdaten)", value: report.n_skipped })}
          ${widgetCard({ title: "Log Loss", value: report.log_loss !== null ? fmtNum(report.log_loss, 4) : "nicht verfügbar" })}
          ${widgetCard({ title: "Simulierte kumulierte Rendite", value: fmtNum(report.cumulative_return, 3) })}
          ${widgetCard({ title: "Max. Drawdown", value: fmtNum(report.max_drawdown, 3) })}
        </div>
        ${_notSignificantNote(report.n_evaluated)}
        <div class="widget-grid">
          ${widgetCard({ title: "YES-Empfehlungen: Trades", value: report.performance_yes.n_trades ?? 0, sub: `Rendite ${fmtNum(report.performance_yes.cumulative_return, 3)}` })}
          ${widgetCard({ title: "NO-Empfehlungen: Trades", value: report.performance_no.n_trades ?? 0, sub: `Rendite ${fmtNum(report.performance_no.cumulative_return, 3)}` })}
        </div>
      </details>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}
