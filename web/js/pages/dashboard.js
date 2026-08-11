function _opportunityRow(o) {
  const edge = o.net_yes_edge;
  return `
    <tr onclick="location.hash='#/market/${encodeURIComponent(o.market_id)}'" style="cursor:pointer">
      <td>${o.question}</td>
      <td>${fmtPct(o.market_yes_probability)}</td>
      <td>${fmtPct(o.estimated_yes_probability)}</td>
      <td>${fmtEdgePp(edge)}</td>
      <td>${fmtNum(o.confidence_score, 0)}</td>
      <td>${fmtDeadline(o.deadline_hours)}</td>
      <td>${statusBadge(o.status)}</td>
    </tr>
  `;
}

function _miniList(items, emptyText) {
  if (!items.length) return `<div class="empty-state">${emptyText}</div>`;
  return `<ul>${items
    .map(
      (o) =>
        `<li><a href="#/market/${encodeURIComponent(o.market_id)}">${o.question}</a> — ${statusBadge(o.status)} · Edge ${fmtEdgePp(o.net_yes_edge)} · Deadline ${fmtDeadline(o.deadline_hours)}</li>`
    )
    .join("")}</ul>`;
}

async function _runScan(button) {
  const original = button.textContent;
  button.textContent = "Scan läuft…";
  button.disabled = true;
  try {
    const result = await Api.scan();
    const parts = result.providers
      .map((p) => (p.error ? `${p.provider}: Fehler (${p.error})` : `${p.provider}: ${p.markets_read} gelesen, ${p.snapshots_saved} gespeichert`))
      .join(" · ");
    alert(`Aktualisierung abgeschlossen. ${parts}`);
    await renderDashboardPage(document.getElementById("content"));
  } catch (err) {
    alert(`Aktualisierung fehlgeschlagen: ${err.message}`);
  } finally {
    button.textContent = original;
    button.disabled = false;
  }
}

// Block H Part 4: real content from the new forecast-semantics
// architecture (analyzed/no-forecast/watch counts, calibration) rather
// than only raw system counters. Sourced from the real /markets and
// /evaluation/forecast-history endpoints — no fabricated numbers.
function _architectureOverviewHtml(marketItems, evalData) {
  const analyzed = marketItems.filter((m) => m.forecast_status && m.forecast_status !== "NO_FORECAST").length;
  const published = marketItems.filter((m) => m.published_forecast_probability !== null && m.published_forecast_probability !== undefined).length;
  const noForecast = marketItems.filter((m) => !m.forecast_status || m.forecast_status === "NO_FORECAST").length;
  const cal = evalData || {};
  return `
    <div class="panel">
      <h3>Prognose-Architektur</h3>
      <div class="widget-grid">
        ${widgetCard({ title: "Analysierte Märkte", value: analyzed })}
        ${widgetCard({ title: "Veröffentlichte Prognosen", value: published })}
        ${widgetCard({ title: "Ohne Prognose (ehrlich)", value: noForecast })}
        ${widgetCard({ title: "Kalibrierungsstatus", value: cal.status || "–" })}
        ${widgetCard({ title: "Aufgelöste Fälle (gesamt)", value: cal.matched_pair_count !== undefined ? cal.matched_pair_count : "–" })}
        ${widgetCard({ title: "Brier-Score", value: cal.brier_score !== null && cal.brier_score !== undefined ? fmtNum(cal.brier_score, 3) : "–" })}
      </div>
      <p class="sub">"Ohne Prognose" ist ein ehrliches Ergebnis, kein Fehler — siehe Datenqualität je Markt.</p>
    </div>
  `;
}

async function renderDashboardPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Übersicht…</div>`;
  try {
    const [cc, marketsResult, evalData] = await Promise.all([
      Api.commandCenter(),
      Api.markets({ limit: 500 }).catch(() => ({ items: [] })),
      Api.evaluationForecastHistory().catch(() => null),
    ]);
    const u = cc.uebersicht;
    const architectureHtml = _architectureOverviewHtml(marketsResult.items || [], evalData);

    container.innerHTML = `
      <div class="disclaimer">Research-Hinweis – keine Wettaufforderung, kein sicherer Gewinn. Alle Werte werden von der eigenen Prognose-Engine berechnet, nicht von einer KI erfunden.</div>

      <div class="panel">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div class="widget-grid" style="flex:1">
            ${widgetCard({ title: "Aktive Märkte", value: u.aktive_maerkte })}
            ${widgetCard({ title: "Märkte mit Preis", value: u.maerkte_mit_preis })}
            ${widgetCard({ title: "Ausreichende Datenqualität", value: u.maerkte_mit_ausreichender_datenqualitaet })}
            ${widgetCard({ title: "Watchlist", value: u.watchlist_anzahl })}
          </div>
        </div>
        <div class="sub">Letzter Scan: ${cc.letzter_scan ? fmtDate(cc.letzter_scan) : "noch nie"}</div>
        <button class="btn" id="scan-btn">Märkte aktualisieren</button>
      </div>

      ${architectureHtml}

      <div class="panel">
        <h3>Interessanteste Märkte jetzt</h3>
        ${
          cc.interessanteste_maerkte.length
            ? `<table><thead><tr><th>Frage</th><th>Markt</th><th>Engine</th><th>Edge</th><th>Confidence</th><th>Deadline</th><th>Status</th></tr></thead>
              <tbody>${cc.interessanteste_maerkte.map(_opportunityRow).join("")}</tbody></table>`
            : `<div class="empty-state">Aktuell keine Märkte mit klarer Priorität — meist ein normaler Zustand, kein Fehler.</div>`
        }
        <p class="sub"><a href="#/opportunities">Alle Chancen ansehen →</a></p>
      </div>

      <div class="panel">
        <h3>Kurz vor Entscheidung</h3>
        ${_miniList(cc.kurz_vor_entscheidung, "Keine Märkte innerhalb der nächsten 7 Tage.")}
      </div>

      <div class="widget-grid">
        <div class="panel">
          <h3>Größte Preisbewegungen</h3>
          ${_miniList(cc.groesste_preisbewegungen, "Noch keine Vergleichsdaten — Historie wird gesammelt.")}
        </div>
        <div class="panel">
          <h3>Höchste Liquidität</h3>
          ${_miniList(cc.hoechste_liquiditaet, "Keine Märkte mit Preis vorhanden.")}
        </div>
      </div>

      <div class="widget-grid">
        <div class="panel">
          <h3>Größte Modellabweichung</h3>
          ${_miniList(cc.groesste_modellabweichung, "Keine auffälligen Abweichungen.")}
        </div>
        <div class="panel">
          <h3>Neue Märkte (24h)</h3>
          ${_miniList(cc.neue_maerkte, "Keine neuen Märkte in den letzten 24 Stunden.")}
        </div>
      </div>

      <div class="panel">
        <h3>Märkte mit Datenproblemen</h3>
        ${_miniList(cc.maerkte_mit_datenproblemen, "Keine offensichtlichen Datenprobleme.")}
      </div>

      <div class="panel">
        <h3>Letzte KI-Auswertungen</h3>
        ${
          cc.letzte_ki_auswertungen.length
            ? `<table><thead><tr><th>Markt</th><th>Modell</th><th>Status</th><th>Zeitpunkt</th></tr></thead>
              <tbody>${cc.letzte_ki_auswertungen
                .map((r) => `<tr><td><a href="#/market/${encodeURIComponent(r.market_id)}">${r.market_id}</a></td><td>${r.model}</td><td>${r.status || "–"}</td><td>${fmtDate(r.created_at)}</td></tr>`)
                .join("")}</tbody></table>`
            : `<div class="empty-state">Noch keine KI-Analysen durchgeführt.</div>`
        }
      </div>
    `;

    document.getElementById("scan-btn").onclick = (e) => _runScan(e.target);
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler beim Laden der Übersicht: ${err.message}</div>`;
  }
}
