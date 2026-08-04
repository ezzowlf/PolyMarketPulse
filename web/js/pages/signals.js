async function renderSignalsPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Shadow-Setups…</div>`;
  try {
    const [aktive, aufgeloest] = await Promise.all([
      Api.shadowSetups("aktiv"),
      Api.shadowSetups("aufgelöst"),
    ]);

    if (aktive.length === 0 && aufgeloest.length === 0) {
      container.innerHTML = `
        <div class="disclaimer">Ein Shadow-Setup entsteht nur, wenn mehrere unabhängige Faktoren gleichzeitig auffällig sind — keine rohen technischen Signale. Warum sehe ich das? Diese Seite zeigt bewusst nur Märkte, die diese hohe Schwelle erreicht haben.</div>
        <div class="empty-state">Aktuell keine Shadow-Setups erkannt. Nach dem nächsten Scan (\`polymarketpulse scan\`) erscheinen hier neue Treffer, sobald mehrere Faktoren gleichzeitig auffällig sind.</div>
      `;
      return;
    }

    container.innerHTML = `
      <div class="disclaimer">Ein Shadow-Setup entsteht nur, wenn mehrere unabhängige Faktoren gleichzeitig auffällig sind — keine rohen technischen Signale, keine Handlungsempfehlung.</div>
      <h2>Aktive Shadow-Setups (${aktive.length})</h2>
      ${aktive.length ? aktive.map(_renderShadowCard).join("") : `<div class="empty-state">Keine aktiven Shadow-Setups.</div>`}
      <h2>Historie (aufgelöst)</h2>
      ${aufgeloest.length ? aufgeloest.map(_renderShadowCard).join("") : `<div class="empty-state">Noch keine aufgelösten Shadow-Setups.</div>`}
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}

function _renderShadowCard(s) {
  const statusBadge = s.status === "aktiv" ? "green" : "";
  return `
    <div class="panel" style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <h3 style="margin:0"><a href="#/market/${encodeURIComponent(s.market_id)}">${s.question || s.provider_market_id}</a></h3>
        <span class="badge ${statusBadge}">Shadow-Score ${s.score.toFixed(0)} · ${s.status}</span>
      </div>
      <div class="sub">Erkannt am ${fmtDate(s.created_at)} · ${s.confirming_factor_count} unabhängige Bestätigung(en)</div>
      <div style="margin-top:8px"><strong>Warum interessant:</strong>
        <ul>${s.warum_interessant.map((g) => `<li>${g}</li>`).join("")}</ul>
      </div>
      ${s.warum_nicht.length ? `<div><strong>Warum nicht (Gegenargumente):</strong><ul>${s.warum_nicht.map((g) => `<li>${g}</li>`).join("")}</ul></div>` : ""}
      ${s.was_fehlt.length ? `<div><strong>Was noch fehlt:</strong><ul>${s.was_fehlt.map((g) => `<li>${g}</li>`).join("")}</ul></div>` : ""}
      ${
        s.status !== "aktiv"
          ? `<div class="sub">Ergebnis: ${s.final_outcome || "–"} · Dauer: ${s.duration_hours !== null ? Math.round(s.duration_hours) + " Std." : "–"}</div>`
          : ""
      }
      <details style="margin-top:8px">
        <summary class="sub" style="cursor:pointer">Wie setzt sich der Shadow-Score zusammen? (keine Blackbox)</summary>
        <ul class="sub">
          <li>Liquidität: ${s.breakdown.liquiditaet.toFixed(1)} Punkte</li>
          <li>Preisbewegung: ${s.breakdown.preisbewegung.toFixed(1)} Punkte</li>
          <li>Volumen: ${s.breakdown.volumen.toFixed(1)} Punkte</li>
          <li>Datenqualität: ${s.breakdown.datenqualitaet.toFixed(1)} Punkte</li>
          <li>News-Relevanz: ${s.breakdown.news_relevanz.toFixed(1)} Punkte</li>
          <li>Historische Vergleichbarkeit: ${s.breakdown.historische_vergleichbarkeit.toFixed(1)} Punkte</li>
          <li>Plattform-Abweichung: ${s.breakdown.cross_provider_abweichung.toFixed(1)} Punkte</li>
          <li>Bestätigungen: ${s.breakdown.bestaetigungen.toFixed(1)} Punkte</li>
        </ul>
      </details>
      <a href="#/research?market=${encodeURIComponent(s.market_id)}">🧠 KI-Analyse zu diesem Markt →</a>
    </div>
  `;
}
