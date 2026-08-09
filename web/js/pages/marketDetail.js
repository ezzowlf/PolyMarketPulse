const RECOMMENDATION_LABEL_DE = {
  STRONG_YES: "klar für YES",
  YES: "für YES",
  WATCH_YES: "beobachten (leichter YES-Vorteil)",
  NO_BET: "keine Wette",
  WATCH_NO: "beobachten (leichter NO-Vorteil)",
  NO: "für NO",
  STRONG_NO: "klar für NO",
  INSUFFICIENT_DATA: "unzureichende Datenlage",
};

const DIRECTION_LABEL_DE = { YES: "YES", NO: "NO", NONE: "keine Wette" };

const DEADLINE_PHASE_LABEL_DE = {
  MORE_THAN_7_DAYS: "mehr als 7 Tage",
  SEVEN_DAYS: "7 Tage",
  SEVENTY_TWO_HOURS: "72 Stunden",
  TWENTY_FOUR_HOURS: "24 Stunden",
  SIX_HOURS: "6 Stunden",
  ONE_HOUR: "1 Stunde",
  FINAL_MINUTES: "letzte Minuten",
  UNKNOWN: "unbekannt",
  RESOLVED_OR_PAST: "Auflösungszeitpunkt erreicht/überschritten",
};

const FALLBACK_REASON_LABEL_DE = [
  { match: /deaktiviert/i, text: "KI nicht aktiviert" },
  { match: /API-Key|key/i, text: "API-Key fehlt" },
  { match: /Kosten|Budget|budget/i, text: "Budget erreicht" },
  { match: /Token-Limit/i, text: "Eingabe zu groß für das Kostenlimit" },
  { match: /.*/, text: "Antwort konnte nicht validiert werden" },
];

function _fallbackLabel(reason) {
  if (!reason) return "Datenlage unzureichend";
  const hit = FALLBACK_REASON_LABEL_DE.find((r) => r.match.test(reason));
  return hit ? hit.text : reason;
}

async function renderMarketDetailPage(container, marketId) {
  container.innerHTML = `<div class="empty-state">Lade Markt…</div>`;
  try {
    const [market, historyFull] = await Promise.all([Api.market(marketId), Api.historyFull(marketId)]);
    const history = historyFull.points;
    const a = historyFull.analytics;
    const latest = market.latest || {};
    const opp = market.opportunity;

    container.innerHTML = `
      <div class="disclaimer">Research-Hinweis – keine Wettaufforderung, kein sicherer Gewinn. Alle Werte werden von der eigenen Prognose-Engine berechnet.</div>

      <div class="panel" id="headline-panel"><div class="empty-state">Lade Hauptaussage…</div></div>
      <div class="panel" id="summary-panel"></div>
      <div class="panel" id="breakdown-panel"></div>

      <div class="panel">
        <p style="color:var(--text-dim)">${market.description || ""}</p>
        <div class="widget-grid">
          ${widgetCard({ title: "Datenquelle", value: market.provider })}
          ${widgetCard({ title: "Liquidität", value: "$" + fmtNum(latest.liquidity) })}
          ${widgetCard({ title: "24h-Volumen", value: "$" + fmtNum(latest.volume_24h) })}
          ${widgetCard({ title: "Spread", value: fmtPct(latest.spread) })}
          ${widgetCard({ title: "Auflösung", value: fmtDate(market.end_date) })}
          ${widgetCard({ title: "Marktstatus", value: fmtStatus(market.resolution_status) })}
        </div>
        <p><a href="${market.url}" target="_blank" rel="noopener">Zur Originalplattform →</a></p>
        <button class="btn" id="add-watchlist">Zur Watchlist hinzufügen</button>
      </div>

      <div class="panel" id="changes-panel"></div>
      <div class="panel" id="evidence-panel"></div>

      <div class="panel" id="prediction-panel">
        <h3>KI-Einschätzung</h3>
        <div class="empty-state">Lade KI-Einschätzung…</div>
      </div>

      <div class="panel" id="scenarios-panel"></div>
      <div class="panel" id="submodels-panel"></div>
      <div class="panel" id="data-quality-panel"></div>

      <div class="panel">
        <h3>Verlauf</h3>
        <h4>Marktpreis (YES)</h4>
        <canvas class="chart-canvas" id="price-chart"></canvas>
        <h4>Research-Score-Verlauf</h4>
        <canvas class="chart-canvas" id="score-chart"></canvas>
        ${history.length < 3 ? `<p class="sub">Historie wird seit Aufnahme dieses Marktes gesammelt — noch wenige Datenpunkte vorhanden.</p>` : ""}
      </div>

      <div class="panel">
        <h3>Relevante Ereignisse</h3>
        ${
          market.news.length
            ? `<table><thead><tr><th>Zeitpunkt</th><th>Quelle</th><th>Überschrift</th><th>Relevanz</th></tr></thead><tbody>
              ${market.news.map((n) => `<tr><td>${fmtDate(n.published_at)}</td><td>${n.source}</td><td>${n.title}</td><td>${(n.confidence * 100).toFixed(0)}%</td></tr>`).join("")}
              </tbody></table>`
            : `<div class="empty-state">Keine mit diesem Markt verknüpften Nachrichten.</div>`
        }
      </div>

      <div class="panel">
        <h3>Signalhistorie <span class="sub">(Erweitert)</span></h3>
        ${
          market.signals.length
            ? `<table><thead><tr><th>Zeit</th><th>Typ</th><th>Score</th><th>Status</th></tr></thead><tbody>
          ${market.signals
            .map((s) => `<tr><td>${fmtDate(s.captured_at)}</td><td>${s.signal_type}</td><td>${fmtNum(s.score, 1)}</td><td>${s.status}</td></tr>`)
            .join("")}
          </tbody></table>`
            : `<div class="empty-state">Keine Signale erfasst.</div>`
        }
      </div>
    `;

    _renderChangesPanel(opp);

    renderLineChart(
      document.getElementById("price-chart"),
      history.map((h) => ({ y: h.yes_price, label: fmtDate(h.captured_at) })),
      { decimals: 3 }
    );
    renderLineChart(
      document.getElementById("score-chart"),
      history.map((h) => ({ y: h.opportunity_score, label: fmtDate(h.captured_at) })),
      { color: "#2fd67f", decimals: 1 }
    );

    document.getElementById("add-watchlist").onclick = async () => {
      await Api.addWatchlist({ provider: market.provider, provider_market_id: market.provider_market_id });
      alert("Zur Watchlist hinzugefügt.");
    };

    renderPredictionPanel(marketId, market);
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}

function _renderChangesPanel(opp) {
  const panel = document.getElementById("changes-panel");
  const c = opp && opp.change_since_last_analysis;
  if (!c) {
    panel.innerHTML = `<h3>Seit letzter Analyse</h3><div class="empty-state">Noch keine vorherige Analyse vorhanden — Vergleich ab der nächsten Aktualisierung möglich.</div>`;
    return;
  }
  const row = (label, obj, fmt) =>
    obj.from === null || obj.to === null
      ? ""
      : `<li>${label}: ${fmt(obj.from)} → ${fmt(obj.to)}</li>`;
  panel.innerHTML = `
    <h3>Seit letzter Analyse <span class="sub">(${fmtDate(c.previous_analysis_at)})</span></h3>
    <ul>
      ${row("Marktpreis", c.market_yes_probability, fmtPct)}
      ${row("Eigene Prognose", c.estimated_yes_probability, fmtPct)}
      ${row("Edge", c.net_yes_edge, fmtEdgePp)}
      ${row("Confidence", c.confidence_score, (v) => fmtNum(v, 0))}
    </ul>
  `;
}

function _headlinePanelHtml(market, opp, pred) {
  const p = pred || {};
  const statusLabel = FORECAST_STATUS_LABEL_DE[p.forecast_status] || p.forecast_status || "Datenlage unzureichend";
  const hasIndependent = p.independent_probability !== null && p.independent_probability !== undefined;
  return `
    <h2 style="margin:0 0 4px">${market.question}</h2>
    <div class="widget-grid">
      ${widgetCard({ title: "MARKT", value: fmtPct(p.market_yes_probability) })}
      ${widgetCard({ title: "UNABHÄNGIG", value: hasIndependent ? fmtPct(p.independent_probability) : "—" })}
      ${widgetCard({ title: "FINAL", value: fmtPct(p.calibrated_probability !== undefined ? p.calibrated_probability : p.estimated_yes_probability) })}
      ${widgetCard({ title: "EDGE", value: fmtEdgePp(p.net_yes_edge) })}
      ${widgetCard({ title: "CONFIDENCE", value: p.confidence_score !== undefined ? `${fmtNum(p.confidence_score, 1)} / 100 <span class="sub">(${p.confidence_calibration_status || "UNCALIBRATED"})</span>` : "–" })}
      ${widgetCard({ title: "DATA QUALITY", value: p.data_quality_score !== undefined ? `${fmtNum(p.data_quality_score, 0)} / 100` : "–" })}
      ${widgetCard({ title: "STATUS", value: statusLabel })}
      ${widgetCard({ title: "FORECAST MATURITY", value: p.forecast_maturity || "–" })}
      ${widgetCard({ title: "Deadline", value: opp ? fmtDeadline(opp.deadline_hours) : "–" })}
    </div>
    ${!hasIndependent ? `<p class="sub">Keine unabhängigen Daten — Prognose basiert derzeit nicht auf einer eigenständigen Analyse.</p>` : ""}
  `;
}

// FORECAST_STATUS_LABEL_DE is defined once in opportunities.js (loaded
// earlier in index.html) and reused here as a plain global — avoids a
// duplicate `const` redeclaration, which is a fatal SyntaxError when both
// files load as global (non-module) scripts on the same page.

const SOURCE_LABEL_DE = {
  history: "Historische Basisrate", momentum: "Marktbewegung", news: "News",
  independent_evidence: "Unabhängige Evidenz", event_relations: "Event-Beziehungen",
  politics: "Politik", geopolitics: "Geopolitik", macro: "Makro", quant: "Quant", sports: "Sport",
};

// I1: sources whose independent forecast doesn't take the market price as
// input at all — used to build the plain-language "independent model type"
// label from whichever of these actually contributed this time.
const INDEPENDENT_SOURCE_NAMES = new Set(["history", "independent_evidence"]);

function _independentModelTypeLabel(p) {
  const contributing = (p.contribution_breakdown || []).filter(
    (c) => INDEPENDENT_SOURCE_NAMES.has(c.source) && c.available && c.estimated_yes_probability !== null
  );
  if (!contributing.length) return "keine (keine unabhängigen Quellen verfügbar)";
  return contributing.map((c) => SOURCE_LABEL_DE[c.source] || c.source).join(" + ");
}

function _independentBreakdownHtml(p) {
  if (!p || !p.contribution_breakdown || !p.contribution_breakdown.length) return "";
  const rows = p.contribution_breakdown.map((c) => {
    const eligibility = c.eligible === false ? "nicht in Frage kommend" : c.eligible === true ? "in Frage kommend" : "generisch (immer in Frage kommend)";
    const used = c.available ? "verwendet" : "nicht verwendet";
    return `
    <tr>
      <td>${SOURCE_LABEL_DE[c.source] || c.source}</td>
      <td class="sub">${eligibility}</td>
      <td>${used}</td>
      <td>${c.available && c.estimated_yes_probability !== null ? fmtPct(c.estimated_yes_probability) : "—"}</td>
      <td>${c.available && c.weight_share !== null ? (c.weight_share * 100).toFixed(0) + "%" : "—"}</td>
      <td>${c.prior_provenance || "—"}</td>
      <td class="sub">${c.detail}</td>
    </tr>
  `;
  }).join("");
  return `
    <h3>Forecast Sources</h3>
    <p class="sub">Unabhängiges Modell: <strong>${_independentModelTypeLabel(p)}</strong></p>
    <table>
      <thead><tr><th>Quelle</th><th>Eligibility</th><th>Status</th><th>Schätzung</th><th>Gewichtsanteil</th><th>Prior-Herkunft</th><th>Details</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="sub">
      Markt: ${fmtPct(p.market_consensus_probability)} |
      Unabhängig: ${p.independent_probability !== null && p.independent_probability !== undefined ? fmtPct(p.independent_probability) : "—"} |
      Kombiniert: ${p.blended_probability !== null && p.blended_probability !== undefined ? fmtPct(p.blended_probability) : "—"} |
      Final (kalibriert): ${p.calibrated_probability !== null && p.calibrated_probability !== undefined ? fmtPct(p.calibrated_probability) : "—"}
    </p>
  `;
}

// I4: divergence red-team audit panel — only rendered when the audit
// actually ran (divergence_audit present).
function _divergenceAuditHtml(p) {
  const audit = p && p.divergence_audit;
  if (!audit) return "";
  const VERDICT_BADGE = { PASS: "green", WARN: "yellow", REJECT: "red" };
  const rows = (audit.checks || []).map((c) => `
    <tr>
      <td>${c.name}</td>
      <td><span class="badge ${VERDICT_BADGE[c.verdict] || ""}">${c.verdict}</span></td>
      <td>${c.hard_fail ? "ja" : "nein"}</td>
      <td class="sub">${c.detail}</td>
    </tr>
  `).join("");
  return `
    <h3>Divergenz-Audit</h3>
    <div class="widget-grid">
      ${widgetCard({ title: "Verdikt", value: `<span class="badge ${VERDICT_BADGE[audit.verdict] || ""}">${audit.verdict || "—"}</span>` })}
      ${widgetCard({ title: "Divergenz (Gap)", value: audit.gap !== null && audit.gap !== undefined ? fmtEdgePp(audit.gap) : "—" })}
    </div>
    <p class="sub">${audit.summary}</p>
    ${rows ? `<table><thead><tr><th>Prüfung</th><th>Verdikt</th><th>Hard Fail</th><th>Begründung</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
  `;
}

const DATA_GAP_CATEGORY_LABEL_DE = {
  HISTORICAL_COMPARABLE: "Historische Vergleichsfälle",
  TIME_HORIZON: "Zeithorizont-Kompatibilität",
  SOURCE_HEALTH: "Quellenverfügbarkeit",
  STRUCTURED_DATA: "Strukturierte Daten",
  EVENT_GRAPH: "Event-Beziehungen",
};

const DATA_GAP_SEVERITY_BADGE = { CRITICAL: "red", HIGH: "red", MEDIUM: "yellow", LOW: "" };

// World State (yes/no condition, deadline, remaining time, counter-evidence)
// — purely diagnostic fields computed by prediction/world_state.py, exposed
// on PredictionResult.world_state but previously never rendered anywhere in
// the frontend. Shown whenever a market has a real deadline/condition to
// report; renders "keine Daten" per-field rather than hiding the whole
// section, per the same honesty convention as the rest of this page.
function _worldStateHtml(p) {
  const ws = p && p.world_state;
  if (!ws) return "";
  const statusCounts = ws.claim_status_counts && Object.keys(ws.claim_status_counts).length
    ? Object.entries(ws.claim_status_counts).map(([k, v]) => `${k}: ${v}`).join(", ")
    : "keine Daten";
  return `
    <h3>Weltzustand</h3>
    <div class="widget-grid">
      ${widgetCard({ title: "YES-Bedingung", value: ws.yes_condition || "keine Daten" })}
      ${widgetCard({ title: "NEIN-Bedingung", value: ws.no_condition || "keine Daten" })}
      ${widgetCard({ title: "Deadline", value: ws.deadline || "keine Daten" })}
      ${widgetCard({ title: "Verbleibende Zeit", value: fmtDeadline(ws.time_remaining_hours) })}
      ${widgetCard({ title: "Widerspruchs-Evidenz", value: String(ws.counter_evidence_count ?? 0) })}
      ${widgetCard({ title: "Claim-Status", value: statusCounts })}
    </div>
    ${
      ws.most_recent_evidence_headline
        ? `<p class="sub">Letzte Evidenz: ${ws.most_recent_evidence_headline} (${fmtDate(ws.most_recent_evidence_published_at)})</p>`
        : ""
    }
  `;
}

// Data Gaps — the severity-tagged list from data_gaps.py's real gap
// detection, computed every run but previously never surfaced in the UI.
// Distinguishes "computed, zero gaps found" from "not computed at all"
// (data_gaps is None) rather than collapsing both into one empty state.
function _dataGapsHtml(p) {
  const dg = p && p.data_gaps;
  if (!dg) {
    return `<h3>Daten-Lücken</h3><div class="empty-state">Nicht berechnet für diesen Lauf.</div>`;
  }
  if (!dg.gaps || !dg.gaps.length) {
    return `<h3>Daten-Lücken</h3><div class="empty-state">Keine Lücken gefunden (0 von 0).</div>`;
  }
  const rows = dg.gaps.map((g) => `
    <tr>
      <td>${DATA_GAP_CATEGORY_LABEL_DE[g.category] || g.category}</td>
      <td><span class="badge ${DATA_GAP_SEVERITY_BADGE[g.severity] || ""}">${g.severity}</span></td>
      <td class="sub">${g.description}</td>
    </tr>
  `).join("");
  const s = dg.summary || {};
  return `
    <h3>Daten-Lücken <span class="sub">(${s.total ?? dg.gaps.length} gesamt — ${s.kritisch ?? 0} kritisch, ${s.hoch ?? 0} hoch, ${s.mittel ?? 0} mittel, ${s.niedrig ?? 0} niedrig)</span></h3>
    <table>
      <thead><tr><th>Kategorie</th><th>Schweregrad</th><th>Beschreibung</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// I3: real historical comparable cases behind the history submodel's
// weighted baseline (question / similarity / outcome / weight).
function _historicalComparablesHtml(p) {
  const cases = p && p.historical_comparables;
  if (!cases || !cases.length) return "";
  const rows = cases.map((c) => `
    <tr>
      <td>${c.question}</td>
      <td>${(c.similarity_score * 100).toFixed(0)}%</td>
      <td>${c.outcome}</td>
      <td>${c.weight_share !== null && c.weight_share !== undefined ? (c.weight_share * 100).toFixed(0) + "%" : "—"}</td>
    </tr>
  `).join("");
  return `
    <h3>Historische Vergleichsfälle</h3>
    <table>
      <thead><tr><th>Frage</th><th>Ähnlichkeit</th><th>Ausgang</th><th>Gewichtsanteil</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function _summaryPanelHtml(p) {
  if (!p || p.estimated_yes_probability === null || p.estimated_yes_probability === undefined) {
    return `<h3>Zusammenfassung</h3><div class="empty-state">Noch keine belastbare Prognose möglich — siehe Datenqualität unten.</div>`;
  }
  const marketPct = fmtPct(p.market_yes_probability);
  const enginePct = fmtPct(p.estimated_yes_probability);
  const edgePp = p.net_yes_edge !== null ? Math.abs(p.net_yes_edge * 100).toFixed(1) : null;
  const hasEdge = p.net_yes_edge !== null && Math.abs(p.net_yes_edge) >= 0.03;
  const direction = p.net_yes_edge > 0 ? "höher" : "niedriger";
  const confidenceWord = p.confidence_score >= 70 ? "hoch" : p.confidence_score >= 40 ? "mittel" : "gering";
  const biggestRisk =
    p.recommendation === "INSUFFICIENT_DATA"
      ? "Zu wenige historische Vergleichsfälle für eine robuste Prognose."
      : p.confidence_score < 40
      ? "Geringes Modellvertrauen — die Prognose kann sich mit neuen Daten stark ändern."
      : "Neue Nachrichten oder Marktbewegungen können die Einschätzung schnell verändern.";

  const sentences = [
    `Der Markt preist aktuell eine YES-Wahrscheinlichkeit von ${marketPct} ein.`,
    `Die eigene Engine kommt auf ${enginePct}.`,
    hasEdge
      ? `Das ist ${edgePp} Prozentpunkte ${direction} als der Marktpreis — eine mögliche Abweichung.`
      : `Es gibt keine belastbare Abweichung zum Marktpreis.`,
    `Das Modellvertrauen ist ${confidenceWord} (${fmtNum(p.confidence_score, 0)}/100).`,
    `Größtes Risiko: ${biggestRisk}`,
  ];
  return `<h3>Zusammenfassung</h3><p>${sentences.join(" ")}</p>`;
}

function _submodelSectionHtml(p) {
  const submodels = p.submodel_estimates || [];
  if (!submodels.length) return "";
  const LABELS = { history: "Historische Vergleichsfälle", momentum: "Marktbewegung", news: "News" };
  const plainLanguage = (s) => {
    if (!s.available) return "noch nicht ausreichend";
    if (s.name === "momentum") {
      if (/stabil/i.test(s.detail)) return "neutral";
      if (/Mean-Reversion/i.test(s.detail)) return "Gegenbewegung möglich";
      return "leichte Bewegung erkennbar";
    }
    if (s.name === "news") return s.estimated_yes_probability !== null ? "Nachrichten fließen ein" : "keine ausreichenden Daten";
    return "einbezogen";
  };
  return `
    <h3>Einflussfaktoren</h3>
    <p class="sub">Deadline-Phase: ${DEADLINE_PHASE_LABEL_DE[p.deadline_phase] || p.deadline_phase}</p>
    <ul>
      ${submodels.map((s) => `<li><strong>${LABELS[s.name] || s.name}:</strong> ${plainLanguage(s)}</li>`).join("")}
      <li><strong>Bayesianisches Update:</strong> ${p.news_sentiment_score ? `Stimmung ${p.news_sentiment_score.toFixed(2)} (-1 negativ .. +1 positiv)` : "geringe Veränderung (keine Nachrichtenevidenz)"}</li>
    </ul>
    <details>
      <summary>Details (technische Werte)</summary>
      <table>
        <thead><tr><th>Modell</th><th>Verfügbar</th><th>Schätzung YES</th><th>Ensemble-Gewicht</th></tr></thead>
        <tbody>${submodels.map((s) => `<tr><td>${s.name}</td><td>${s.available ? "ja" : "nein"}</td><td>${s.estimated_yes_probability !== null ? fmtPct(s.estimated_yes_probability) : "–"}</td><td>${fmtNum(s.weight, 2)}</td></tr>`).join("")}</tbody>
      </table>
    </details>
  `;
}

function _scenarioSectionHtml(scenarios) {
  if (!scenarios) return "";
  return `
    <h3>Szenarien</h3>
    <p><strong>Basisszenario:</strong> ${scenarios.base_case}</p>
    ${scenarios.bull_case && scenarios.bull_case.length ? `<p><strong>Bull Case:</strong></p><ul>${scenarios.bull_case.map((s) => `<li>${s}</li>`).join("")}</ul>` : ""}
    ${scenarios.bear_case && scenarios.bear_case.length ? `<p><strong>Bear Case:</strong></p><ul>${scenarios.bear_case.map((s) => `<li>${s}</li>`).join("")}</ul>` : ""}
  `;
}

function _dataQualityPanelHtml(p) {
  const dq = p.data_quality_breakdown || {};
  const total = p.data_quality_score;
  const level = total >= 75 ? "Hoch" : total >= 45 ? "Mittel" : "Niedrig";
  const reasons = [];
  reasons.push(p.market_yes_probability !== null ? "Preis vorhanden" : "Preis fehlt");
  if (dq.liquiditaet !== undefined) reasons.push(dq.liquiditaet >= 60 ? "gute Liquidität" : "geringe Liquidität");
  if (dq.historische_fallzahl !== undefined) reasons.push(dq.historische_fallzahl >= 40 ? "ausreichend historische Vergleichsfälle" : "wenig historische Vergleichsfälle");
  if (dq.quellenuebereinstimmung !== undefined) reasons.push(dq.quellenuebereinstimmung >= 60 ? "gute News-Abdeckung" : "keine ausreichende News-Historie");
  if (dq.aktualitaet !== undefined) reasons.push(dq.aktualitaet >= 70 ? "aktuell beobachtet" : "Markt erst kurz beobachtet");

  // I6: J2/K1 composite breakdowns (per-dimension raw/normalized/
  // availability), shown as expandable detail alongside the existing
  // single-number legacy display (kept unchanged above).
  const compositeTable = (composite, label) => {
    if (!composite) return "";
    const rows = composite.dimensions.map((d) => `
      <tr>
        <td>${d.name}</td>
        <td>${d.available ? "ja" : "nein"}</td>
        <td>${d.raw_value !== null && d.raw_value !== undefined ? fmtNum(d.raw_value, 2) : "—"}</td>
        <td>${d.normalized_score !== null && d.normalized_score !== undefined ? fmtNum(d.normalized_score, 0) : "—"}</td>
        <td class="sub">${d.reason}</td>
      </tr>
    `).join("");
    return `
      <details>
        <summary>${label}: ${fmtNum(composite.score, 0)} / 100 — Komposit-Aufschlüsselung</summary>
        <table>
          <thead><tr><th>Dimension</th><th>Verfügbar</th><th>Rohwert</th><th>Normiert (0-100)</th><th>Begründung</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <p class="sub">${composite.formula_detail}</p>
      </details>
    `;
  };

  return `
    <h3>Datenqualität: ${level}</h3>
    <ul>${reasons.map((r) => `<li>${r}</li>`).join("")}</ul>
    <p class="sub">Confidence-Kalibrierung: <strong>${p.confidence_calibration_status || "UNCALIBRATED"}</strong> — noch nicht anhand realer aufgelöster Prognosen validiert.</p>
    <details>
      <summary>Details (technische Aufschlüsselung, Legacy)</summary>
      <table>
        <thead><tr><th>Vollständigkeit</th><th>Aktualität</th><th>Quellenübereinstimmung</th><th>Historische Fallzahl</th><th>Resolution-Klarheit</th><th>Liquidität</th></tr></thead>
        <tbody><tr>
          <td>${fmtNum(dq.vollstaendigkeit, 0)}</td><td>${fmtNum(dq.aktualitaet, 0)}</td><td>${fmtNum(dq.quellenuebereinstimmung, 0)}</td>
          <td>${fmtNum(dq.historische_fallzahl, 0)}</td><td>${fmtNum(dq.resolution_klarheit, 0)}</td><td>${fmtNum(dq.liquiditaet, 0)}</td>
        </tr></tbody>
      </table>
    </details>
    ${compositeTable(p.data_quality_composite, "Datenqualität (J2-Komposit)")}
    ${compositeTable(p.confidence_composite, "Confidence (K1-Komposit)")}
  `;
}

function _evidenceSectionHtml(p) {
  const ie = p && p.independent_evidence;
  // I3/I4 (historical comparables, divergence audit) are independent of
  // whether independent-evidence itself was available — always append them
  // so they're never silently hidden just because news evidence was thin.
  const extras = `${_worldStateHtml(p)}${_dataGapsHtml(p)}${_historicalComparablesHtml(p)}${_divergenceAuditHtml(p)}`;
  if (!ie) {
    return `<h3>Unabhängige Evidenz</h3><div class="empty-state">Keine unabhängige Schätzung möglich — keine unabhängige Evidenz-Infrastruktur verfügbar.</div>${extras}`;
  }
  if (!ie.available) {
    return `<h3>Unabhängige Evidenz</h3><div class="empty-state">keine unabhängige Schätzung möglich — ${ie.detail}</div>${extras}`;
  }
  const marketPct = fmtPct(p.market_yes_probability);
  const independentPct = fmtPct(ie.independent_yes_probability);
  const divergenceStr = ie.divergence !== null ? fmtEdgePp(ie.divergence) : "–";

  // I2: per-item evidence table — source / relation / relevance / source
  // quality / freshness / direction / impact, not a generic bullet list.
  const evidenceTable = (items) => {
    if (!items || !items.length) return `<p class="sub">keine</p>`;
    const rows = items.map((e) => `
      <tr>
        <td><a href="${e.url}" target="_blank" rel="noopener">${e.title}</a><div class="sub">${e.source_domain || e.source} — ${fmtDate(e.published_at)}</div></td>
        <td>${e.relation_label}</td>
        <td>${e.entailment}</td>
        <td>${(e.link_confidence * 100).toFixed(0)}%</td>
        <td>${(e.reliability * 100).toFixed(0)}%</td>
        <td>${(e.recency_weight * 100).toFixed(0)}%</td>
        <td>${(e.relation_weight * 100).toFixed(0)}%</td>
      </tr>
    `).join("");
    return `<table><thead><tr><th>Quelle / Ereignis</th><th>Relation</th><th>Richtung</th><th>Relevanz</th><th>Quellqualität</th><th>Aktualität</th><th>Impact/Gewicht</th></tr></thead><tbody>${rows}</tbody></table>`;
  };

  const discarded = ie.discarded_evidence || [];

  return `
    <h3>Unabhängige Evidenz</h3>
    <div class="widget-grid">
      ${widgetCard({ title: "MARKT", value: marketPct })}
      ${widgetCard({ title: "UNABHÄNGIGES MODELL", value: independentPct })}
      ${widgetCard({ title: "DIVERGENZ", value: divergenceStr })}
      ${widgetCard({ title: "Quellenqualität", value: ie.source_quality_score !== null ? `${fmtNum(ie.source_quality_score, 0)} / 100` : "–" })}
      ${widgetCard({ title: "Zeit seit Erstmeldung", value: ie.time_since_first_report_hours !== null ? `${fmtNum(ie.time_since_first_report_hours, 1)} h` : "–" })}
      ${widgetCard({ title: "Information Edge", value: ie.information_edge_score !== null ? `${fmtNum(ie.information_edge_score, 0)} / 100` : "–" })}
    </div>
    ${ie.breaking ? `<div class="badge yellow">Breaking (unter 48h)</div>` : ""}
    ${ie.contradiction_detected ? `<div class="badge yellow">Widersprüchliche Quellenlage</div>` : ""}
    <p><strong>Spricht für YES (${ie.evidence_for_yes.length}):</strong></p>
    ${evidenceTable(ie.evidence_for_yes)}
    <p><strong>Spricht für NO (${ie.evidence_for_no.length}):</strong></p>
    ${evidenceTable(ie.evidence_for_no)}
    ${
      ie.not_yet_priced_in.length
        ? `<p><strong>Noch nicht eingepreist:</strong></p>${evidenceTable(ie.not_yet_priced_in)}`
        : ""
    }
    ${
      discarded.length
        ? `<details><summary>Verworfen / nicht relevant (${discarded.length}) — gesehen, aber nicht in die Schätzung eingeflossen</summary>${evidenceTable(discarded)}</details>`
        : ""
    }
    <p class="sub">${ie.detail}</p>
    ${_resolutionEdgeHtml(p.resolution_edge)}
    ${_crossMarketHtml(p.cross_market)}
    ${_reactionLagHtml(p.reaction_lag)}
    ${_marketFlowHtml(p)}
    ${_worldStateHtml(p)}
    ${_dataGapsHtml(p)}
    ${_historicalComparablesHtml(p)}
    ${_divergenceAuditHtml(p)}
  `;
}

function _marketFlowHtml(p) {
  const rel = p.market_reliability;
  const risk = p.manipulation_risk;
  const ob = p.orderbook_metrics;
  const flow = p.trade_flow_metrics;
  const wallet = p.wallet_concentration;

  return `
    <h4>Marktreliabilität und Orderfluss</h4>
    <div class="widget-grid">
      ${widgetCard({ title: "Marktreliabilität", value: rel ? rel.level : "unzureichende Daten" })}
      ${widgetCard({ title: "Manipulationsrisiko", value: risk ? `${fmtNum(risk.risk_score, 0)} / 100` : "–" })}
    </div>
    <h5>Orderbuch</h5>
    ${
      ob && ob.available
        ? `<div class="widget-grid">
            ${widgetCard({ title: "Spread", value: fmtNum(ob.spread, 4) })}
            ${widgetCard({ title: "Imbalance", value: ob.imbalance !== null ? ob.imbalance.toFixed(2) : "–" })}
            ${widgetCard({ title: "Tiefe (Bid+Ask)", value: "$" + fmtNum((ob.bid_depth || 0) + (ob.ask_depth || 0)) })}
            ${widgetCard({ title: "Dünn", value: ob.thin ? "ja" : "nein" })}
          </div>`
        : `<p class="sub">${ob ? ob.detail : "Orderbuchdaten nicht verfügbar."}</p>`
    }
    <h5>Marktfluss (öffentliche Trades)</h5>
    ${
      flow && flow.available
        ? `<div class="widget-grid">
            ${widgetCard({ title: "Status", value: flow.status })}
            ${widgetCard({ title: "Netto-Flow", value: "$" + fmtNum(flow.net_flow_usd) })}
            ${widgetCard({ title: "Anteil großer Trades", value: flow.large_trade_ratio !== null ? `${(flow.large_trade_ratio * 100).toFixed(0)}%` : "–" })}
          </div><p class="sub">${flow.detail}</p>`
        : `<p class="sub">${flow ? flow.detail : "Trade-Daten nicht verfügbar."}</p>`
    }
    <h5>Öffentliche Positionen</h5>
    ${
      wallet && wallet.available
        ? `<div class="widget-grid">
            ${widgetCard({ title: "Konzentration", value: `${fmtNum(wallet.concentration_score, 0)} / 100` })}
            ${widgetCard({ title: "Größte Adresse", value: wallet.top1_share !== null ? `${(wallet.top1_share * 100).toFixed(0)}%` : "–" })}
          </div>
          <p class="sub">Gekürzte Top-Adressen: ${wallet.top_wallets.join(", ")} — keine Identitätszuordnung.</p>`
        : `<p class="sub">${wallet ? wallet.detail : "Positionsdaten nicht verfügbar."}</p>`
    }
    ${
      risk && risk.reasons.length
        ? `<h5>Risikohinweise</h5><ul>${risk.reasons.map((r) => `<li>${r}</li>`).join("")}</ul><p class="sub">${risk.detail}</p>`
        : ""
    }
  `;
}

function _resolutionEdgeHtml(re) {
  if (!re) return "";
  return `
    <h4>Resolution-Kriterien</h4>
    <div class="widget-grid">
      ${widgetCard({ title: "Resolution-Risiko", value: re.risk_level })}
      ${widgetCard({ title: "Resolution Edge Score", value: `${fmtNum(re.resolution_edge_score, 0)} / 100` })}
      ${widgetCard({ title: "Explizite Frist", value: re.has_explicit_deadline ? "ja" : "nein" })}
      ${widgetCard({ title: "Zuständige Quelle", value: re.authority_source || "unbekannt" })}
    </div>
    <p><strong>YES-Bedingung:</strong> ${re.yes_condition}</p>
    <p><strong>NO-Bedingung:</strong> ${re.no_condition}</p>
    ${re.pitfalls.length ? `<p><strong>Stolperfallen:</strong></p><ul>${re.pitfalls.map((p2) => `<li>${p2}</li>`).join("")}</ul>` : ""}
  `;
}

function _crossMarketHtml(cm) {
  if (!cm) return "";
  if (!cm.available) return `<h4>Cross-Market</h4><p class="sub">${cm.detail}</p>`;
  return `
    <h4>Cross-Market-Widersprüche</h4>
    <p class="sub">${cm.detail}</p>
    ${
      cm.related_markets.length
        ? `<ul>${cm.related_markets.map((m) => `<li>(${fmtNum(m.overlap_confidence * 100, 0)}% Überlappung) ${m.question} — ${m.yes_price !== null ? fmtPct(m.yes_price) : "kein Preis"} <span class="sub">(${m.provider})</span></li>`).join("")}</ul>`
        : ""
    }
  `;
}

function _reactionLagHtml(rl) {
  if (!rl) return "";
  return `
    <h4>Marktreaktion</h4>
    <div class="widget-grid">
      ${widgetCard({ title: "Status", value: rl.status })}
      ${widgetCard({ title: "Reaktionsdauer", value: rl.reaction_detected_at_hours !== null ? `${fmtNum(rl.reaction_detected_at_hours, 1)} h` : "–" })}
    </div>
    <p class="sub">${rl.detail}</p>
  `;
}

function _aiCardHtml(response) {
  const p = response.prediction;
  const e = response.explanation;
  const meta = response.meta;
  const cost = meta.actual_cost_usd ?? meta.estimated_cost_usd;
  const fallbackText = meta.used_fallback ? _fallbackLabel(meta.fallback_reason) : null;

  return `
    <h3>KI-Einschätzung</h3>
    <div class="widget-grid">
      ${widgetCard({ title: "Modell", value: meta.model })}
      ${widgetCard({ title: "Status", value: meta.used_fallback ? `<span class="badge yellow">${fallbackText}</span>` : `<span class="badge green">erfolgreich</span>` })}
      ${widgetCard({ title: "Zeitpunkt", value: fmtDate(meta.created_at) })}
      ${widgetCard({ title: "Tokens", value: meta.input_tokens !== null ? `${meta.input_tokens} in / ${meta.output_tokens} out` : "–" })}
      ${widgetCard({ title: "Kosten", value: cost !== null && cost !== undefined ? "$" + Number(cost).toFixed(5) : "–" })}
      ${widgetCard({ title: "Aus Cache", value: meta.cached ? "ja" : "nein" })}
    </div>
    ${
      meta.used_fallback && /deaktiviert/i.test(meta.fallback_reason || "")
        ? `<div class="disclaimer">KI ist deaktiviert. In den <a href="#/settings">Einstellungen</a> aktivieren (erfordert OPENAI_API_KEY in .env und Neustart).</div>`
        : ""
    }
    <h4>${e.headline}</h4>
    <p>${e.summary}</p>
    <p>${e.recommendation_explanation}</p>
    ${e.supports_yes && e.supports_yes.length ? `<p><strong>Was spricht für YES (Bull Case):</strong></p><ul>${e.supports_yes.map((f) => `<li>[${f.impact}] ${f.factor}</li>`).join("")}</ul>` : ""}
    ${e.supports_no && e.supports_no.length ? `<p><strong>Was spricht für NO (Bear Case):</strong></p><ul>${e.supports_no.map((f) => `<li>[${f.impact}] ${f.factor}</li>`).join("")}</ul>` : ""}
    ${e.uncertainties && e.uncertainties.length ? `<p><strong>Was beobachten:</strong> ${e.uncertainties.join("; ")}</p>` : ""}
    <p class="sub">${e.warning}</p>
    <button class="btn" id="recompute-prediction">KI-Analyse aktualisieren</button>
  `;
}

async function renderPredictionPanel(marketId, market) {
  const panel = document.getElementById("prediction-panel");
  const headlinePanel = document.getElementById("headline-panel");
  const summaryPanel = document.getElementById("summary-panel");
  const scenariosPanel = document.getElementById("scenarios-panel");
  const submodelsPanel = document.getElementById("submodels-panel");
  const dqPanel = document.getElementById("data-quality-panel");
  const evidencePanel = document.getElementById("evidence-panel");
  const breakdownPanel = document.getElementById("breakdown-panel");
  if (!panel) return;

  const paint = (response) => {
    headlinePanel.innerHTML = _headlinePanelHtml(market, market.opportunity, response.prediction);
    summaryPanel.innerHTML = _summaryPanelHtml(response.prediction);
    if (breakdownPanel) breakdownPanel.innerHTML = _independentBreakdownHtml(response.prediction);
    if (evidencePanel) evidencePanel.innerHTML = _evidenceSectionHtml(response.prediction);
    panel.innerHTML = _aiCardHtml(response);
    scenariosPanel.innerHTML = _scenarioSectionHtml(response.prediction.scenarios);
    submodelsPanel.innerHTML = _submodelSectionHtml(response.prediction);
    dqPanel.innerHTML = _dataQualityPanelHtml(response.prediction);
    document.getElementById("recompute-prediction").onclick = async () => {
      panel.innerHTML = `<h3>KI-Einschätzung</h3><div class="empty-state">Berechne neu…</div>`;
      try {
        const fresh = await Api.explainRecommendationRecompute(marketId);
        paint(fresh);
      } catch (err) {
        panel.innerHTML = `<h3>KI-Einschätzung</h3><div class="empty-state">Fehler: ${err.message}</div>`;
      }
    };
  };

  try {
    const response = await Api.explainRecommendation(marketId);
    paint(response);
  } catch (err) {
    headlinePanel.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
    panel.innerHTML = `<h3>KI-Einschätzung</h3><div class="empty-state">Fehler: ${err.message}</div>`;
  }
}
