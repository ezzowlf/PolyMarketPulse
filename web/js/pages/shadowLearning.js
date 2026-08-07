function _shadowRow(t) {
  return `
    <tr>
      <td><a href="#/market/${encodeURIComponent(t.market_id)}">${t.market_id}</a></td>
      <td>${t.direction}</td>
      <td>${t.status}</td>
      <td>${t.entry_market_price !== null ? fmtPct(t.entry_market_price) : "–"}</td>
      <td>${t.expected_edge !== null ? fmtEdgePp(t.expected_edge) : "–"}</td>
      <td>${t.confidence !== null ? fmtNum(t.confidence, 0) : "–"}</td>
      <td>${t.simulated_pnl !== null && t.simulated_pnl !== undefined ? fmtNum(t.simulated_pnl, 3) : "–"}</td>
      <td>${t.exit_reason || "–"}</td>
    </tr>
  `;
}

function _blockersHtml(blockers) {
  if (!blockers || !blockers.length) return `<p class="sub">Keine Blocker erfasst.</p>`;
  return `<ul>${blockers.map((b) => `<li>${b.blocker} <span class="sub">(${b.count}x)</span></li>`).join("")}</ul>`;
}

function _breakdownTable(title, rows, labelKey) {
  if (!rows || !rows.length) return "";
  return `
    <h4>${title}</h4>
    <table><thead><tr><th>Bucket</th><th>n</th><th>Trefferquote</th><th>Ø P&L</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${r[labelKey]}</td><td>${r.n}</td><td>${r.hit_rate !== null ? (r.hit_rate * 100).toFixed(0) + "%" : "–"}</td><td>${r.average_pnl !== null && r.average_pnl !== undefined ? r.average_pnl.toFixed(3) : "–"}</td></tr>`).join("")}
    </tbody></table>
  `;
}

async function renderShadowLearningPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Shadow-Daten…</div>`;
  try {
    const [perf, trades] = await Promise.all([Api.shadowPerformance(), Api.shadowPositions()]);
    const p = perf.performance;
    const submodels = perf.submodels;
    const active = trades.filter((t) => t.status === "candidate" || t.status === "active");
    const closed = trades.filter((t) => t.status === "closed").slice(0, 30);

    container.innerHTML = `
      <div class="disclaimer">Reine Simulation — keine echten Orders, kein echtes Geld. Alle Werte sind Shadow-/Backtest-Größen.</div>

      <div class="panel">
        <h3>Shadow-Performance</h3>
        <div class="widget-grid">
          ${widgetCard({ title: "Kandidaten gesamt", value: p.n_candidates })}
          ${widgetCard({ title: "Übersprungen", value: p.n_skipped })}
          ${widgetCard({ title: "Aktiv", value: p.n_active })}
          ${widgetCard({ title: "Geschlossen", value: p.n_closed })}
          ${widgetCard({ title: "Trefferquote", value: p.hit_rate !== null ? (p.hit_rate * 100).toFixed(0) + "%" : "–" })}
          ${widgetCard({ title: "Brier Score", value: p.brier_score !== null ? p.brier_score : "–" })}
          ${widgetCard({ title: "Gesamt-P&L (simuliert)", value: p.total_pnl !== null ? p.total_pnl : "–" })}
          ${widgetCard({ title: "Ø ROI", value: p.average_roi !== null ? (p.average_roi * 100).toFixed(1) + "%" : "–" })}
          ${widgetCard({ title: "Max. Drawdown", value: p.max_drawdown !== null ? p.max_drawdown : "–" })}
          ${widgetCard({ title: "Ø Haltedauer", value: p.average_holding_hours !== null ? p.average_holding_hours.toFixed(1) + " h" : "–" })}
        </div>
        <button class="btn" id="shadow-scan-btn">Shadow-Scan jetzt ausführen</button>
        <p class="sub">Prüft alle offenen Märkte gegen die konfigurierten Mindestbedingungen und erzeugt neue Kandidaten oder protokolliert die Blocker.</p>
      </div>

      <div class="panel">
        ${_breakdownTable("Ergebnisse nach Confidence", p.breakdown_by_confidence, "bucket")}
        ${_breakdownTable("Ergebnisse nach Opportunity Score", p.breakdown_by_opportunity_score, "bucket")}
      </div>

      <div class="panel">
        <h4>Ergebnisse nach Richtung</h4>
        <table><thead><tr><th>Richtung</th><th>n</th><th>Trefferquote</th></tr></thead><tbody>
          ${p.breakdown_by_direction.map((r) => `<tr><td>${r.direction}</td><td>${r.n}</td><td>${r.hit_rate !== null ? (r.hit_rate * 100).toFixed(0) + "%" : "–"}</td></tr>`).join("")}
        </tbody></table>
        <h4>Ergebnisse nach Deadline-Phase</h4>
        <table><thead><tr><th>Phase</th><th>n</th><th>Trefferquote</th></tr></thead><tbody>
          ${p.breakdown_by_deadline_phase.map((r) => `<tr><td>${r.phase}</td><td>${r.n}</td><td>${r.hit_rate !== null ? (r.hit_rate * 100).toFixed(0) + "%" : "–"}</td></tr>`).join("")}
        </tbody></table>
      </div>

      <div class="panel">
        <h4>Häufigste Blocker (übersprungene Kandidaten)</h4>
        ${_blockersHtml(p.most_common_blockers)}
      </div>

      <div class="panel">
        <h3>Modellvergleich (pro Submodell)</h3>
        <table><thead><tr><th>Submodell</th><th>n auswertbar</th><th>Brier</th><th>Trefferquote</th></tr></thead><tbody>
          ${submodels.map((s) => `<tr><td>${s.name}</td><td>${s.n_evaluable}</td><td>${s.brier_score !== null ? s.brier_score : "–"}</td><td>${s.hit_rate !== null ? (s.hit_rate * 100).toFixed(0) + "%" : "–"}</td></tr>`).join("")}
        </tbody></table>
        ${!submodels.length ? `<p class="sub">Noch keine aufgelösten Märkte mit gespeicherter Submodell-Aufschlüsselung.</p>` : ""}
      </div>

      <div class="panel">
        <h3>Aktive / Kandidaten-Setups</h3>
        ${
          active.length
            ? `<table><thead><tr><th>Markt</th><th>Richtung</th><th>Status</th><th>Einstieg</th><th>Edge</th><th>Confidence</th><th>P&L</th><th>Exit-Grund</th></tr></thead><tbody>${active.map(_shadowRow).join("")}</tbody></table>`
            : `<div class="empty-state">Keine aktiven Shadow-Setups. Führe einen Shadow-Scan aus.</div>`
        }
      </div>

      <div class="panel">
        <h3>Zuletzt geschlossene Setups</h3>
        ${
          closed.length
            ? `<table><thead><tr><th>Markt</th><th>Richtung</th><th>Status</th><th>Einstieg</th><th>Edge</th><th>Confidence</th><th>P&L</th><th>Exit-Grund</th></tr></thead><tbody>${closed.map(_shadowRow).join("")}</tbody></table>`
            : `<div class="empty-state">Noch keine geschlossenen Setups.</div>`
        }
      </div>
    `;

    document.getElementById("shadow-scan-btn").onclick = async () => {
      const btn = document.getElementById("shadow-scan-btn");
      btn.disabled = true;
      btn.textContent = "Läuft…";
      try {
        await Api.shadowScan();
        await renderShadowLearningPage(container);
      } catch (err) {
        alert("Fehler: " + err.message);
        btn.disabled = false;
        btn.textContent = "Shadow-Scan jetzt ausführen";
      }
    };
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}
