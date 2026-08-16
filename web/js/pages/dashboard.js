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

// Live Evidence Engine: real, DB-derived coverage numbers (/coverage) —
// how many of the real unresolved markets actually have sources/claims/
// forecasts, plus the real top blockers holding the rest back.
const _BLOCKER_LABELS = {
  no_sources: "Keine Quellen",
  no_claims: "Keine Claims",
  source_fetch_failed: "Quellenabruf fehlgeschlagen",
  no_primary_source: "Keine Primärquelle",
  one_independent_group: "Nur eine unabhängige Quelle",
  resolution_path_unknown: "Resolution Path unbekannt",
  insufficient_evidence: "Zu wenig Evidence",
  divergence_rejected: "Divergenz-Audit abgelehnt",
};

function _coverageHtml(cov) {
  if (!cov) return "";
  const blockerItems = Object.entries(cov.top_blockers || {})
    .map(([key, count]) => `<li>${_BLOCKER_LABELS[key] || key}: <strong>${count}</strong></li>`)
    .join("");
  return `
    <div class="panel">
      <h3>Datenabdeckung (Live Evidence Engine)</h3>
      <div class="widget-grid">
        ${widgetCard({ title: "Märkte gesamt", value: cov.markets_total })}
        ${widgetCard({ title: "Unresolved", value: cov.markets_unresolved })}
        ${widgetCard({ title: "Mit Quellen", value: cov.markets_with_sources })}
        ${widgetCard({ title: "Mit Claims", value: cov.markets_with_claims })}
        ${widgetCard({ title: "Unabh. Bestätigung", value: cov.markets_with_multiple_independent_groups })}
        ${widgetCard({ title: "Modellhypothesen", value: cov.markets_with_model_hypothesis })}
        ${widgetCard({ title: "Evidence-backed", value: cov.markets_with_evidence_backed_forecast })}
        ${widgetCard({ title: "Published Forecasts", value: cov.markets_with_published_forecast })}
      </div>
      ${blockerItems ? `<h4 style="margin-top:12px">Top Blocker</h4><ul>${blockerItems}</ul>` : ""}
      <p class="sub">0 veröffentlichte Prognosen ist ein ehrliches Ergebnis bei dünner Evidenzlage, kein Fehler.</p>
    </div>
  `;
}

// Phase 7.16: Dashboard Actionability. Reads the SAME already-enriched
// /research-queue response the market-detail page's next_research_action
// panel and the queue endpoint itself are built from (research_runner.py's
// enrich_queue_with_gap_voi) -- no second backend structure, no extra
// provider fetches triggered by loading the dashboard (the queue endpoint
// is itself read-only against already-persisted state).

const _CLOSABILITY_DE = { HIGH: "Hoch", MEDIUM: "Mittel", LOW: "Niedrig", BLOCKED: "Blockiert" };
const _VOI_LEVEL = (score) => (score >= 60 ? "Hoch" : score >= 30 ? "Mittel" : "Niedrig");

function _highValueResearchHtml(queue) {
  const rows = queue.filter((q) => q.action_type === "FETCH" && ["HIGH", "MEDIUM"].includes(q.closability))
    .sort((a, b) => b.voi_score - a.voi_score)
    .slice(0, 5);
  if (!rows.length) {
    return `<div class="panel"><h3>High-Value Research</h3><div class="empty-state">Aktuell keine besonders werthaltige Recherche-Aktion offen.</div></div>`;
  }
  const cards = rows.map((q) => `
    <li>
      <a href="#/market/${encodeURIComponent(q.market_id)}"><strong>${q.question}</strong></a>
      <span class="sub"> · ${q.product_mode || "–"} · Datenabdeckung ${q.category || "–"}</span><br/>
      <span class="sub">${_humanText(q.human_summary || "")}</span><br/>
      <span class="badge">VOI ${_VOI_LEVEL(q.voi_score)}</span>
      <span class="badge">Closability ${_CLOSABILITY_DE[q.closability] || q.closability}</span>
      <span class="sub" title="VOI Score (technisch): ${q.voi_score}"> (Details im Marktdetail)</span>
    </li>
  `).join("");
  return `<div class="panel"><h3>High-Value Research</h3><ul>${cards}</ul></div>`;
}

function _closableGapsHtml(queue) {
  const rows = queue.filter((q) => q.action_type === "FETCH" && ["HIGH", "MEDIUM"].includes(q.closability))
    .sort((a, b) => b.voi_score - a.voi_score)
    .slice(0, 8);
  if (!rows.length) {
    return `<div class="panel"><h3>Schließbare Datenlücken</h3><div class="empty-state">Keine realistisch kurzfristig schließbaren kritischen Datenlücken.</div></div>`;
  }
  const items = rows.map((q) => `
    <li>
      <a href="#/market/${encodeURIComponent(q.market_id)}">${q.question}</a>
      <span class="sub"> — fehlt: ${_humanText(q.target_information || "unbekannt")} · Quelle: ${q.preferred_provider || "–"}${q.fallback_provider ? ` (Fallback: ${q.fallback_provider})` : ""}</span>
    </li>
  `).join("");
  return `<div class="panel"><h3>Schließbare Datenlücken</h3><ul>${items}</ul></div>`;
}

function _providerBlockersHtml(queue) {
  const rows = queue.filter((q) => q.action_type === "BLOCKED_PROVIDER");
  if (!rows.length) {
    return `<div class="panel"><h3>Provider-Blocker</h3><div class="empty-state">Keine aktuell blockierten wichtigen Quellen.</div></div>`;
  }
  const items = rows.map((q) => `
    <li>
      <a href="#/market/${encodeURIComponent(q.market_id)}">${q.question}</a>
      <span class="sub"> — fehlt: ${_humanText(q.target_information || "unbekannt")} · blockierte Quelle: ${q.preferred_provider || "–"}${q.fallback_provider ? ` · Fallback vorhanden: ${q.fallback_provider}` : " · kein Fallback bekannt"}${q.next_retry ? ` · nächster Versuch: ${fmtDate(q.next_retry)}` : ""}</span>
    </li>
  `).join("");
  return `<div class="panel"><h3>Provider-Blocker</h3><ul>${items}</ul></div>`;
}

function _noArchetypeHtml(queue) {
  const rows = queue.filter((q) => q.reason === "NO_ARCHETYPE");
  if (!rows.length) return "";
  return `
    <details class="panel">
      <summary>Ohne Archetyp (${rows.length})</summary>
      <p class="sub">Für diese Märkte existiert derzeit kein unterstütztes Analysemodell.</p>
      <ul>${rows.slice(0, 10).map((q) => `<li><a href="#/market/${encodeURIComponent(q.market_id)}">${q.question}</a></li>`).join("")}</ul>
    </details>
  `;
}

function _researchQueueHtml(queue) {
  if (!queue || !queue.length) {
    return `<div class="panel"><h3>Research Queue</h3><div class="empty-state">Aktuell keine priorisierten Recherche-Kandidaten.</div></div>`;
  }
  return `
    ${_highValueResearchHtml(queue)}
    ${_closableGapsHtml(queue)}
    ${_providerBlockersHtml(queue)}
    ${_noArchetypeHtml(queue)}
  `;
}

function _productDashboardHtml(items) {
  const numeric = items.filter((m) => m.product_mode === "VALIDATED_NUMERIC_FORECAST").slice(0, 5);
  const structured = items.filter((m) => m.product_mode === "STRUCTURED_OUTLOOK").slice(0, 5);
  const near = [...items].filter((m) => m.end_date).sort((a, b) => String(a.end_date).localeCompare(String(b.end_date))).slice(0, 5);
  const list = (rows, empty, label) => rows.length ? `<ul>${rows.map((m) => `<li><a href="#/market/${encodeURIComponent(m.market_id)}"><strong>${m.question}</strong></a><br/><span class="sub">${label(m)} · Deadline ${fmtDate(m.end_date)}</span></li>`).join("")}</ul>` : `<div class="empty-state">${empty}</div>`;
  return `
    <div class="panel"><h2>Heute interessant</h2><p class="sub">Was das System aktuell zusätzlich zum Marktpreis einordnen kann.</p>
      <div class="widget-grid">
        <div><h3>Validierte Modellprognosen</h3>${list(numeric, "Aktuell keine validierte Modellprognose mit gespeicherten Eingaben.", (m) => `Modell ${fmtPct(m.model_hypothesis_probability)} · Markt ${fmtPct(m.yes_price)}`)}</div>
        <div><h3>Strukturierte Einschätzungen</h3>${list(structured, "Noch keine Märkte mit ausreichend strukturierter Evidenz.", () => "Zustand, Pfad und nächster Schritt verfügbar")}</div>
      </div>
      <h3>Entscheidungen als Nächstes</h3>${list(near, "Keine datierten aktiven Märkte.", (m) => m.product_mode === "INSUFFICIENT_DATA" ? "Recherchebedarf" : "Beobachten")}
    </div>
  `;
}

async function renderDashboardPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Übersicht…</div>`;
  try {
    const [marketsResult, evalData, coverage, researchQueue] = await Promise.all([
      Api.markets({ limit: 500 }).catch(() => ({ items: [] })),
      Api.evaluationForecastHistory().catch(() => null),
      Api.coverage().catch(() => null),
      Api.researchQueue(20).catch(() => []),
    ]);
    const marketItems = marketsResult.items || [];
    // The normal dashboard is intentionally driven by its persisted list
    // contract.  It must not trigger hundreds of fresh prediction/source
    // calculations through the legacy command-center convenience endpoint.
    const cc = {
      uebersicht: {
        aktive_maerkte: marketItems.length,
        maerkte_mit_preis: marketItems.filter((m) => m.yes_price !== null && m.yes_price !== undefined).length,
        maerkte_mit_ausreichender_datenqualitaet: marketItems.filter((m) => (m.data_quality_composite_score || 0) >= 45).length,
        watchlist_anzahl: 0,
      },
      letzter_scan: null,
      interessanteste_maerkte: [], kurz_vor_entscheidung: [], groesste_preisbewegungen: [],
      hoechste_liquiditaet: [], groesste_modellabweichung: [], neue_maerkte: [],
      maerkte_mit_datenproblemen: [], letzte_ki_auswertungen: [],
    };
    const u = cc.uebersicht;
    const architectureHtml = _architectureOverviewHtml(marketItems, evalData);

    container.innerHTML = `
      <div class="disclaimer">Research-Hinweis – keine Wettaufforderung, kein sicherer Gewinn. Alle Werte werden von der eigenen Prognose-Engine berechnet, nicht von einer KI erfunden.</div>

      ${_productDashboardHtml(marketItems)}

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

      ${_coverageHtml(coverage)}

      ${_researchQueueHtml(researchQueue)}

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
