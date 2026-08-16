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

const DECISION_STATE_LABEL_DE = {
  NO_POSITION: "Keine Position",
  WATCH: "Beobachten",
  POSSIBLE_EDGE: "Mögliche Edge",
  STRONG_EDGE: "Starke Edge",
};
const DECISION_STATE_BADGE = {
  NO_POSITION: "", WATCH: "yellow", POSSIBLE_EDGE: "yellow", STRONG_EDGE: "green",
};

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
      <div class="panel" id="why-panel"></div>
      <div class="panel" id="evidence-panel"></div>
      <div class="panel" id="change-triggers-panel"></div>
      <div class="panel" id="scenarios-panel"></div>
      <div class="panel" id="future-map-panel"></div>
      <div class="panel" id="sensitivity-panel"></div>
      <div class="panel" id="forecast-history-panel"><div class="empty-state">Lade Forecast-Verlauf…</div></div>
      <div class="panel" id="historical-reliability-panel"><div class="empty-state">Lade historische Zuverlässigkeit…</div></div>

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

      <details class="panel">
        <summary><h3 style="display:inline">Erweitert / Audit</h3></summary>
        <div id="breakdown-panel"></div>
        <div id="prediction-panel">
          <h3>KI-Einschätzung</h3>
          <div class="empty-state">Lade KI-Einschätzung…</div>
        </div>
        <div id="submodels-panel"></div>
        <div id="data-quality-panel"></div>
        <div>
          <h3>Verlauf (Marktpreis / Score)</h3>
          <h4>Marktpreis (YES)</h4>
          <canvas class="chart-canvas" id="price-chart"></canvas>
          <h4>Research-Score-Verlauf</h4>
          <canvas class="chart-canvas" id="score-chart"></canvas>
          ${history.length < 3 ? `<p class="sub">Historie wird seit Aufnahme dieses Marktes gesammelt — noch wenige Datenpunkte vorhanden.</p>` : ""}
        </div>
      </details>

      <details class="panel">
        <summary><h3 style="display:inline">Verknüpfte Nachrichten (Audit)</h3></summary>
        ${
          market.news.length
            ? `<table><thead><tr><th>Zeitpunkt</th><th>Quelle</th><th>Überschrift</th><th>Relevanz</th></tr></thead><tbody>
              ${market.news.map((n) => `<tr><td>${fmtDate(n.published_at)}</td><td>${n.source}</td><td>${n.title}</td><td>${(n.confidence * 100).toFixed(0)}%</td></tr>`).join("")}
              </tbody></table>`
            : `<div class="empty-state">Keine mit diesem Markt verknüpften Nachrichten.</div>`
        }
      </details>

      <details class="panel">
        <summary><h3 style="display:inline">Signalhistorie (Audit)</h3></summary>
        ${_signalHistoryHtml(market.signals)}
      </details>
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

    Api.forecastHistory(marketId).then((rows) => {
      const el = document.getElementById("forecast-history-panel");
      if (el) el.innerHTML = _forecastHistoryHtml(rows);
    }).catch((err) => {
      const el = document.getElementById("forecast-history-panel");
      if (el) el.innerHTML = `<h3>Forecast-Verlauf</h3><div class="empty-state">Fehler: ${err.message}</div>`;
    });

    Api.evaluationForecastHistory().then((evalData) => {
      const el = document.getElementById("historical-reliability-panel");
      if (el) el.innerHTML = _historicalReliabilityHtml(evalData, market.category);
    }).catch((err) => {
      const el = document.getElementById("historical-reliability-panel");
      if (el) el.innerHTML = `<h3>Historische Zuverlässigkeit</h3><div class="empty-state">Fehler: ${err.message}</div>`;
    });
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}

function _signalHistoryHtml(signals) {
  if (!signals || !signals.length) return `<div class="empty-state">Keine Signale erfasst.</div>`;
  const grouped = new Map();
  for (const signal of signals) {
    // Scanner retries can record the same observation more than once.  Keep
    // the audit evidence, but group identical type/score/status/timestamp
    // entries rather than presenting them as independent signals.
    const key = [signal.captured_at, signal.signal_type, signal.score, signal.status].join("|");
    const current = grouped.get(key) || { ...signal, count: 0 };
    current.count += 1;
    grouped.set(key, current);
  }
  return `<table><thead><tr><th>Zeit</th><th>Typ</th><th>Score</th><th>Status</th></tr></thead><tbody>${[...grouped.values()]
    .map((s) => `<tr><td>${fmtDate(s.captured_at)}</td><td>${s.signal_type}${s.count > 1 ? ` × ${s.count}` : ""}</td><td>${fmtNum(s.score, 1)}</td><td>${s.status}</td></tr>`)
    .join("")}</tbody></table>`;
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
      ${row("Veröffentlichte Prognose", c.published_forecast_probability, fmtPct)}
      ${row("Edge", c.net_yes_edge, fmtEdgePp)}
      ${row("Confidence", c.confidence_score, (v) => fmtNum(v, 0))}
    </ul>
  `;
}

function _headlinePanelHtml(market, opp, pred) {
  const p = pred || {};
  const forecastPublished = p.published_forecast_probability !== null && p.published_forecast_probability !== undefined;
  const productNumeric = p.product_mode === "VALIDATED_NUMERIC_FORECAST" && p.product_probability !== null && p.product_probability !== undefined;
  const pulseValue = productNumeric ? fmtPct(p.product_probability) : (forecastPublished ? fmtPct(p.published_forecast_probability) : "Keine belastbare Prognose");
  const quantitativeOnly = _hasQuantitativeSupport(p) && !((p.independent_evidence || {}).evidence_for_yes || []).length && !((p.independent_evidence || {}).evidence_for_no || []).length;
  const pulseSub = productNumeric
    ? "Validiertes Modell · noch keine veröffentlichte Handelsprognose"
    : forecastPublished
    ? (quantitativeOnly ? "Primär quantitativ begründet" : "Evidenzgestützt")
    : "Nicht genügend unabhängige Evidenz";
  const statusLabel = FORECAST_STATUS_LABEL_DE[p.forecast_status] || p.forecast_status || "Datenlage unzureichend";
  const trustLabel = p.confidence_score >= 70 ? "hoch" : p.confidence_score >= 40 ? "mittel" : "gering";
  const dataQualityLabel = p.data_quality_score >= 75 ? "Gut" : p.data_quality_score >= 45 ? "Mittel" : "Schwach";
  const decisionLabel = DECISION_STATE_LABEL_DE[p.decision_state] || p.decision_state || "–";
  const decisionBadge = DECISION_STATE_BADGE[p.decision_state] || "";
  const decisionReasons = p.decision_reasons && p.decision_reasons.length
    ? `<details><summary>Begründung Entscheidungsstatus</summary><ul>${p.decision_reasons.map((r) => `<li class="sub">${r}</li>`).join("")}</ul></details>`
    : "";
  const ws = p.world_state;
  const deadlineValue = ws && ws.time_remaining_hours !== null && ws.time_remaining_hours !== undefined
    ? fmtDeadline(ws.time_remaining_hours)
    : (opp ? fmtDeadline(opp.deadline_hours) : "–");
  const maturityTitle = ["SUPPORTED_FORECAST", "MATURE_FORECAST"].includes(p.forecast_maturity)
    ? "Warum belastbar?"
    : "Warum noch keine belastbare Prognose?";
  const maturityRows = (p.maturity_breakdown || []).map((item) => `
    <tr><td>${_humanText(item.dimension)}</td><td><strong>${item.status}</strong></td><td class="sub">${_humanText(item.reason)}</td></tr>
  `).join("");
  const maturityDetails = maturityRows ? `
    <details open>
      <summary>${maturityTitle}</summary>
      <table><thead><tr><th>Prüfung</th><th>Status</th><th>Begründung</th></tr></thead><tbody>${maturityRows}</tbody></table>
    </details>
  ` : "";
  const availability = p.source_availability;
  const sourceNotice = availability && availability.status === "SOURCE_UNREACHABLE" ? `
    <div class="empty-state" style="border-left:4px solid #c9912f; margin-top:12px">
      <strong>⚠ ${availability.title}</strong><br/><span class="sub">${availability.message}</span>
      <details><summary>Technische Details</summary><p class="sub">Betroffene Quelle(n): ${(availability.provider_attempts || []).filter((a) => a.status === "SOURCE_FETCH_FAILED").map((a) => a.provider).join(", ") || "unbekannt"}<br/>Letzter erfolgreicher Abruf: ${fmtDate(availability.last_successful_fetch)}<br/>Letzter Fehlversuch: ${fmtDate(availability.last_failed_fetch)}<br/>Status: ${availability.retry_status || "–"}; nächste Prüfung: ${fmtDate(availability.next_check_at)}</p></details>
    </div>` : "";
  const earlySignals = (p.early_signals || []).filter((s) => ["RUMOR", "EARLY_SIGNAL", "PARTIALLY_CONFIRMED"].includes(s.signal_status));
  const earlySignalNotice = earlySignals.length ? `<section><h3>Frühe Signale</h3>${earlySignals.slice(0, 3).map((s) => `<div class="empty-state"><strong>⚡ Frühes Signal erkannt</strong> · ${s.provider}<br/>${s.summary}<br/><span class="sub">Status: noch nicht unabhängig bestätigt. Nächster Schritt: passende Primär- und unabhängige Quellen prüfen.</span></div>`).join("")}</section>` : "";
  const coherenceWarnings = (p.coherence || []).filter((item) => item.status === "COHERENCE_WARNING");
  const coherenceNotice = coherenceWarnings.length ? `<div class="empty-state"><strong>Verbundene Märkte wirken inkonsistent.</strong><br/><span class="sub">${coherenceWarnings[0].explanation}</span><details><summary>Beziehungsdetails</summary><p class="sub">${coherenceWarnings[0].relationship} · ${coherenceWarnings[0].other_market_id} · provenance-basiert</p></details></div>` : "";
  return `
    <h2 style="margin:0 0 12px">${market.question}</h2>
    <div class="widget-grid">
      ${widgetCard({ title: "MARKT", value: fmtPct(p.market_yes_probability) })}
      ${widgetCard({ title: productNumeric ? "POLYMARKETPULSE" : "EINSCHÄTZUNG", value: pulseValue, sub: pulseSub })}
      ${widgetCard({ title: "STATUS", value: productNumeric ? "Validiertes Modell" : _researchStatusLabel(p) })}
      ${widgetCard({ title: "EINSCHÄTZUNG", value: `<span class="badge ${decisionBadge}">${decisionLabel}</span>` })}
      ${widgetCard({ title: "VERTRAUEN", value: trustLabel })}
      ${widgetCard({ title: "DATENLAGE", value: dataQualityLabel })}
      ${widgetCard({ title: "DEADLINE", value: deadlineValue })}
    </div>
    ${decisionReasons}
    ${sourceNotice}
    ${earlySignalNotice}
    ${coherenceNotice}
    ${maturityDetails}
    ${_structuredOutlookHtml(p)}
    ${_insufficientDataHtml(p)}
    ${quantitativeOnly ? `<p class="sub">Diese Einschätzung basiert primär auf quantitativen Markt- oder Makrodaten; passende bestätigende Nachrichtenquellen liegen derzeit nicht vor.</p>` : ""}
    ${!forecastPublished ? `<p class="sub">${_noForecastExplanation(p, availability)}</p>` : ""}
    <details><summary>Modell- und Prüfstatus</summary><p class="sub">${productNumeric ? "Fed-Entscheidungsmodell: zeitgetrennt validiert; die Modellschätzung ist oben als PolyMarketPulse-Wert sichtbar." : "Für diesen Markt liegt derzeit kein historisch validiertes numerisches Modell vor."} · ${statusLabel}</p></details>
  `;
}

function _insufficientDataHtml(p) {
  if (p.product_mode !== "INSUFFICIENT_DATA") return "";
  const product = p || {};
  const missing = (product.missing || []).slice(0, 3);
  return `
    <section class="panel" style="margin-top:12px">
      <h3>Was wir bisher wissen</h3>
      <p>${_humanText(product.summary || "Der Markt ist erfasst, aber noch nicht ausreichend belastbar eingeordnet.")}</p>
      <h4>Was fehlt</h4>
      ${missing.length ? `<ul>${missing.map((item) => `<li>${_humanText(item)}</li>`).join("")}</ul>` : "<p>Es liegen noch keine ausreichend marktbezogenen Quellen oder Strukturinformationen vor.</p>"}
      <h4>Nächster Recherche-Schritt</h4>
      <p>${_humanText(product.next_research || "Zuerst wird geprüft, welche Primärquelle die Resolution-Bedingung direkt belegen kann.")}</p>
    </section>
  `;
}

function _structuredOutlookHtml(p) {
  if (p.product_mode !== "STRUCTURED_OUTLOOK") return "";
  const state = p.structured_world_state || p.world_state || {};
  const next = p.next_event || {};
  const scenarioRows = ((p.scenarios || {}).scenarios || []).slice(0, 3).map((s) => `
    <li><strong>${s.outcome === "YES" ? "Positiver Verlauf" : "Negativer Verlauf"}:</strong> ${_humanText(s.description || "Noch keine belastbare Szenariobeschreibung.")}</li>
  `).join("");
  const gaps = (state.data_gaps || []).slice(0, 3).map((g) => `<li>${_humanText(g.description || g)}</li>`).join("");
  return `
    <section class="panel" style="margin-top:12px">
      <h3>Strukturierte Einschätzung</h3>
      <div class="widget-grid">
        ${widgetCard({ title: "Aktueller Stand", value: _humanText(state.current_state || "Noch nicht bestätigt") })}
        ${widgetCard({ title: "Nächster wichtiger Schritt", value: _humanText(next.next_event_description || "Noch nicht eindeutig bestimmbar") })}
      </div>
      ${scenarioRows ? `<h4>Wichtige Szenarien</h4><ul>${scenarioRows}</ul>` : ""}
      ${gaps ? `<h4>Was noch fehlt</h4><ul>${gaps}</ul>` : ""}
      <p class="sub">Keine Modellwahrscheinlichkeit: Für diesen Markt existiert noch kein historisch validiertes numerisches Modell.</p>
    </section>
  `;
}

function _hasQuantitativeSupport(p) {
  return (p.contribution_breakdown || []).some((c) => c.available && ["macro", "quant"].includes(c.source));
}

function _researchStatusLabel(p) {
  const availability = p.source_availability || {};
  if (p.published_forecast_probability !== null && p.published_forecast_probability !== undefined) return "Prognose veröffentlicht";
  if (availability.status === "SOURCE_UNREACHABLE") return "Datenquelle derzeit nicht erreichbar";
  if (p.forecast_status === "FORECAST_SUPPRESSED") return "Prognose noch nicht freigegeben";
  if (p.model_hypothesis_probability !== null && p.model_hypothesis_probability !== undefined) return "Nur Modellschätzung";
  if (availability.status === "NO_RELEVANT_EVIDENCE") return "Keine belastbare Evidenz gefunden";
  if (availability.status === "WEAK_DATA") return "Noch nicht untersucht";
  return "Recherche läuft";
}

function _noForecastExplanation(p, availability) {
  if (availability && availability.status === "SOURCE_UNREACHABLE") return availability.message;
  if (availability && availability.status === "NO_RELEVANT_EVIDENCE") return availability.message;
  if (p.forecast_status === "FORECAST_SUPPRESSED") return "Die Modellschätzung wurde nicht veröffentlicht, weil die Abweichung vom Markt noch nicht ausreichend unabhängig belegt ist.";
  if (_hasQuantitativeSupport(p)) return "Es liegen quantitative Anhaltspunkte vor, aber noch keine ausreichende unabhängige Bestätigung für eine veröffentlichbare Prognose.";
  return "Der Markt wurde untersucht; für eine belastbare Prognose fehlen derzeit ausreichende unabhängige Belege.";
}

// FORECAST_STATUS_LABEL_DE is defined once in opportunities.js (loaded
// earlier in index.html) and reused here as a plain global — avoids a
// duplicate `const` redeclaration, which is a fatal SyntaxError when both
// files load as global (non-module) scripts on the same page.

const SOURCE_LABEL_DE = {
  history: "Historische Basisrate", momentum: "Marktbewegung", news: "News",
  independent_evidence: "Unabhängige Evidenz", event_relations: "Event-Beziehungen",
  politics: "Politik", geopolitics: "Geopolitik", macro: "Makro", macro_policy: "Fed-Policy", quant: "Quant", sports: "Sport",
};

// I1: sources whose independent forecast doesn't take the market price as
// input at all — used to build the plain-language "independent model type"
// label from whichever of these actually contributed this time.
const INDEPENDENT_SOURCE_NAMES = new Set([
  "history", "independent_evidence", "politics", "geopolitics", "macro", "quant", "sports",
]);

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
  const SUPPORT_BADGE = {
    SUPPORTED_DIVERGENCE: "green",
    WEAKLY_SUPPORTED_DIVERGENCE: "yellow",
    UNSUPPORTED_DIVERGENCE: "red",
  };
  const rows = (audit.checks || []).map((c) => `
    <tr>
      <td>${c.name}</td>
      <td><span class="badge ${VERDICT_BADGE[c.verdict] || ""}">${c.verdict}</span></td>
      <td>${c.hard_fail ? "ja" : "nein"}</td>
      <td class="sub">${c.detail}</td>
    </tr>
  `).join("");
  // p.divergence_support: SUPPORTED_DIVERGENCE / WEAKLY_SUPPORTED_DIVERGENCE /
  // UNSUPPORTED_DIVERGENCE (or null if divergence wasn't triggered) — added
  // to PredictionResult by divergence_audit.classify_divergence_support(),
  // previously computed but never rendered anywhere in the UI.
  return `
    <h3>Divergenz-Audit</h3>
    <div class="widget-grid">
      ${widgetCard({ title: "Verdikt", value: `<span class="badge ${VERDICT_BADGE[audit.verdict] || ""}">${audit.verdict || "—"}</span>` })}
      ${widgetCard({ title: "Divergenz (Gap)", value: audit.gap !== null && audit.gap !== undefined ? fmtEdgePp(audit.gap) : "—" })}
      ${widgetCard({ title: "Divergenz-Einstufung", value: p.divergence_support ? `<span class="badge ${SUPPORT_BADGE[p.divergence_support] || ""}">${p.divergence_support}</span>` : "keine Daten" })}
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
  // waterway_state / path_to_resolution: added in a later round
  // (world_state.py's current_state/trend + supporting/blocking conditions,
  // honestly UNKNOWN absent real evidence — "absence of bad news is not
  // normalization"). Previously computed but never rendered anywhere in the
  // UI; shown here as an additive extension of the same section, "keine
  // Daten" per-field rather than hiding the whole card.
  const wws = ws.waterway_state;
  const ptr = ws.path_to_resolution;
  const supporting = ptr && ptr.supporting_conditions && ptr.supporting_conditions.length
    ? ptr.supporting_conditions.join("; ")
    : "keine Daten";
  const blocking = ptr && ptr.blocking_conditions && ptr.blocking_conditions.length
    ? ptr.blocking_conditions.join("; ")
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
      ${widgetCard({ title: "Aktueller Zustand", value: (wws && wws.current_state) || (ptr && ptr.current_state) || "keine Daten" })}
      ${widgetCard({ title: "Trend", value: (wws && wws.trend) || "keine Daten" })}
    </div>
    ${
      ptr
        ? `<p class="sub"><strong>Unterstützende Bedingungen:</strong> ${supporting}<br/><strong>Blockierende Bedingungen:</strong> ${blocking}</p>`
        : ""
    }
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
  if (!p) return "";
  const candidateCount = p.historical_candidate_count || 0;
  const acceptedCount = p.historical_accepted_count || 0;
  const rejectedCount = p.historical_rejected_count || 0;
  const cases = p.historical_comparables;

  // Correctness-hardening round 2 (Part E): when History was evaluated at
  // all (candidateCount > 0) but nothing passed the compatibility gate
  // (acceptedCount === 0 — e.g. the Hormuz market, where 126 candidates
  // were considered and every single one was correctly rejected), show
  // that honestly instead of silently rendering an empty section.
  if (!cases || !cases.length) {
    if (candidateCount > 0) {
      return `
        <h3>Historische Vergleichsfälle</h3>
        <div class="empty-state">Keine ausreichend ähnlichen historischen Vergleichsfälle. (${candidateCount} Kandidat(en) geprüft, ${rejectedCount} abgelehnt, 0 akzeptiert.)</div>
      `;
    }
    return "";
  }

  const rows = cases.map((c) => `
    <tr>
      <td>${c.question}</td>
      <td>${(c.similarity_score * 100).toFixed(0)}%</td>
      <td>${c.outcome}</td>
      <td>${c.weight_share !== null && c.weight_share !== undefined ? (c.weight_share * 100).toFixed(0) + "%" : "—"}</td>
    </tr>
  `).join("");
  return `
    <h3>Historische Vergleichsfälle <span class="sub">(${acceptedCount} akzeptiert / ${candidateCount} geprüft, ${rejectedCount} abgelehnt)</span></h3>
    <table>
      <thead><tr><th>Frage</th><th>Ähnlichkeit</th><th>Ausgang</th><th>Gewichtsanteil</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function _summaryPanelHtml(p) {
  if (!p) {
    return `<h3>Zusammenfassung</h3><div class="empty-state">Noch keine Prognose verfügbar.</div>`;
  }
  const marketPct = fmtPct(p.market_yes_probability);
  const forecastPublished = p.published_forecast_probability !== null && p.published_forecast_probability !== undefined;
  const pulseLabel = forecastPublished ? fmtPct(p.published_forecast_probability) : "keine belastbare Prognose";
  const modelHypothesis = p.model_hypothesis_probability !== null && p.model_hypothesis_probability !== undefined
    ? fmtPct(p.model_hypothesis_probability)
    : "nicht verfügbar";
  const trustLabel = p.confidence_score >= 70 ? "hoch" : p.confidence_score >= 40 ? "mittel" : "gering";

  const keyPoints = [];
  if (forecastPublished) {
    keyPoints.push(`Unsere evidenzgestützte Prognose liegt bei ${pulseLabel}.`);
    keyPoints.push(`Das ist gegenüber dem Marktpreis von ${marketPct} eine differenzierbare Auswertung.`);
    keyPoints.push(`Das Vertrauen in diese Prognose ist ${trustLabel}.`);
  } else {
    keyPoints.push(`Unsere Modellhypothese liegt bei ${modelHypothesis}, aber es gibt aktuell nicht genügend unabhängige Evidenz für eine veröffentlichbare Prognose.`);
    if (p.forecast_status === "FORECAST_SUPPRESSED") {
      keyPoints.push("Eine Divergenz-Sicherheitsprüfung hat die Prognose unterdrückt.");
    } else {
      keyPoints.push("Zu wenige unabhängige Quellen oder zu viele offene Datenlücken für eine belastbare Prognose.");
    }
    keyPoints.push(`Das Modellvertrauen ist ${trustLabel}.`);
  }

  return `
    <h3>Kernaussage</h3>
    <ul class="summary-list">
      ${keyPoints.map((point) => `<li>${point}</li>`).join("")}
    </ul>
  `;
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
  const richScenarios = scenarios.scenarios || [];
  const richHtml = richScenarios.length ? richScenarios.map((s) => `
    <div class="source-card">
      <div class="source-card-title"><strong>${_humanText(s.outcome)}</strong>${s.probability !== null && s.probability !== undefined ? ` · ${fmtPct(s.probability)}` : ""}</div>
      <p>${_humanText(s.description)}</p>
      ${s.necessary_events && s.necessary_events.length ? `<p class="sub"><strong>Notwendige Ereignisse:</strong> ${s.necessary_events.map(_humanText).join("; ")}</p>` : ""}
      ${s.supporting_claims && s.supporting_claims.length ? `<p class="sub"><strong>Stützende Belege:</strong> ${s.supporting_claims.join("; ")}</p>` : ""}
      ${s.contradicting_claims && s.contradicting_claims.length ? `<p class="sub"><strong>Widersprechende Belege:</strong> ${s.contradicting_claims.join("; ")}</p>` : ""}
      ${s.triggers && s.triggers.length ? `<p class="sub"><strong>Trigger:</strong> ${s.triggers.map(_humanTrigger).join("; ")}</p>` : ""}
    </div>
  `).join("") : "";
  return `
    <h3>Szenarien</h3>
    ${richHtml ? `<div class="source-card-grid">${richHtml}</div>` : ""}
    <details${richHtml ? "" : " open"}>
      <summary>Basis-/Bull-/Bear-Case (kurz)</summary>
      <p><strong>Basisszenario:</strong> ${_humanText(scenarios.base_case)}</p>
      ${scenarios.bull_case && scenarios.bull_case.length ? `<p><strong>Bull Case:</strong></p><ul>${scenarios.bull_case.map((s) => `<li>${_humanText(s)}</li>`).join("")}</ul>` : ""}
      ${scenarios.bear_case && scenarios.bear_case.length ? `<p><strong>Bear Case:</strong></p><ul>${scenarios.bear_case.map((s) => `<li>${_humanText(s)}</li>`).join("")}</ul>` : ""}
    </details>
  `;
}

function _futureMapHtml(tree) {
  if (!tree || !tree.branches || !tree.branches.length) {
    return `<h3>Future Map</h3><div class="empty-state">Für diesen Markt ist kein bestätigter mehrstufiger Ereignispfad verfügbar.</div>`;
  }
  const branches = tree.branches.filter((branch) => branch.branch_type !== "CURRENT");
  const row = (branch) => {
    const outcome = branch.outcome === "UNRESOLVED" ? "offen" : _humanText(branch.outcome);
    const prerequisites = branch.prerequisites && branch.prerequisites.length ? ` · nach ${branch.prerequisites.map(_humanText).join(" → ")}` : "";
    return `<li><strong>${_humanText(branch.event)}</strong>${prerequisites} → ${outcome}</li>`;
  };
  return `
    <h3>Future Map</h3>
    <p class="sub">${tree.template_name}: ${_humanText(tree.branches[0].event)}</p>
    <ul>${branches.map(row).join("")}</ul>
    <p class="sub">Die Pfade sind strukturell abgeleitet. Es werden keine Übergangswahrscheinlichkeiten angezeigt, solange keine empirische Kalibrierung vorliegt.</p>
  `;
}

function _sensitivityHtml(audit) {
  if (!audit || audit.baseline_probability === null || audit.baseline_probability === undefined) {
    return `<h3>Robustheit der Prognose</h3><div class="empty-state">Keine gewichtete, unabhängige Basisprognose verfügbar.</div>`;
  }
  const computed = (audit.counterfactuals || []).filter((item) => item.status === "COMPUTED");
  const strongest = audit.strongest_input ? `${audit.strongest_input} (${fmtEdgePp(audit.strongest_delta)})` : "kein einzelner Faktor";
  return `
    <h3>Robustheit der Prognose</h3>
    <p>Basis vor News-Update: <strong>${fmtPct(audit.baseline_probability)}</strong>. Stärkster messbarer Einfluss: <strong>${strongest}</strong>.</p>
    ${audit.fragility === "SINGLE_INPUT" ? `<p class="sub">Fragil: Nur ein gewichteter, unabhängiger Input ist verfügbar.</p>` : ""}
    ${computed.length ? `<details><summary>Einzelne Inputs hypothetisch entfernen</summary><table><thead><tr><th>Ohne</th><th>Neue Basis</th><th>Delta</th></tr></thead><tbody>${computed.map((item) => `<tr><td>${item.removed_input}</td><td>${fmtPct(item.without_probability)}</td><td>${fmtEdgePp(item.delta)}</td></tr>`).join("")}</tbody></table><p class="sub">Diese Deltas sind exakt nur für das lineare Vor-News-Ensemble berechnet. News und Marktpreis werden nicht fälschlich als lineare Inputs dargestellt.</p></details>` : ""}
  `;
}

const INFLUENCE_STRENGTH_ORDER = {
  STRONG_POSITIVE: 3, STRONG_NEGATIVE: 3, MEDIUM_POSITIVE: 2, MEDIUM_NEGATIVE: 2, NEUTRAL: 1,
};
const INFLUENCE_LABEL_DE = {
  STRONG_POSITIVE: "starker YES-Einfluss", STRONG_NEGATIVE: "starker NO-Einfluss",
  MEDIUM_POSITIVE: "mittlerer YES-Einfluss", MEDIUM_NEGATIVE: "mittlerer NO-Einfluss",
  NEUTRAL: "neutral",
};

// Part 1.4 "Why": the max 3-5 most important factors, ranked by the real
// influence_rank on each contribution_breakdown entry (Block D).
function _whySectionHtml(p) {
  const entries = (p && p.contribution_breakdown) || [];
  const ranked = entries
    .filter((c) => c.available && c.influence_rank && c.influence_rank !== "NEUTRAL")
    .sort((a, b) => (INFLUENCE_STRENGTH_ORDER[b.influence_rank] || 0) - (INFLUENCE_STRENGTH_ORDER[a.influence_rank] || 0))
    .slice(0, 5);
  if (!ranked.length) {
    return `<h3>Warum</h3><div class="empty-state">Kein einzelner Faktor sticht mit klarem Einfluss hervor.</div>`;
  }
  return `
    <h3>Warum</h3>
    <ul>
      ${ranked.map((c) => `<li><strong>${SOURCE_LABEL_DE[c.source] || c.source}:</strong> ${INFLUENCE_LABEL_DE[c.influence_rank] || c.influence_rank} — ${c.explanation || c.detail}</li>`).join("")}
    </ul>
  `;
}

// Part 1.7 "What would change our assessment" — Block D's deterministic
// change_triggers list (plain strings, always computed, may be empty).
function _changeTriggersHtml(p) {
  const triggers = (p && p.change_triggers) || [];
  if (!triggers.length) {
    return `<h3>Was würde unsere Einschätzung ändern?</h3><div class="empty-state">Keine konkreten Trigger identifiziert.</div>`;
  }
  return `
    <h3>Was würde unsere Einschätzung ändern?</h3>
    <ul>${triggers.map((t) => `<li>${_humanTrigger(t)}</li>`).join("")}</ul>
  `;
}

function _humanTrigger(trigger) {
  const raw = String(trigger || "");
  const labels = {
    current_state: "eine bestätigte Änderung des aktuellen Ereigniszustands",
    path_step: "ein bestätigter nächster Schritt im Ereignispfad",
    source_fetch_status: "wieder verfügbare oder neue belastbare Quelldaten",
    DIRECT_RESOLUTION: "eine direkte Bestätigung der Auflösungsbedingung",
    PATH_STEP: "ein bestätigter Schritt auf dem Weg zur Auflösung",
    QUANTITATIVE_SIGNAL: "ein deutliches neues quantitatives Signal",
  };
  return _humanText(labels[raw] || raw);
}

function _humanText(value) {
  const replacements = {
    current_state: "aktueller Ereigniszustand", macro_inputs: "aktuelle Makrodaten",
    meeting: "nächste Sitzung", policy_decision: "geldpolitische Entscheidung",
    resolution: "Auflösung des Markts", path_step: "nächster Schritt im Ereignispfad",
    DIRECT_RESOLUTION: "direkte Bestätigung der Auflösungsbedingung", PATH_STEP: "Schritt im Ereignispfad",
    QUANTITATIVE_SIGNAL: "quantitatives Signal", CURRENT_STATE: "Aktueller Ereigniszustand",
    MACRO_INPUTS: "Aktuelle Makrodaten", MEETING: "Nächste Sitzung",
    POLICY_DECISION: "Geldpolitische Entscheidung", RESOLUTION: "Auflösung des Markts",
    house_vote: "Abstimmung im Repräsentantenhaus", senate_vote: "Befassung im Senat",
    LEGISLATION: "Gesetzgebungsverfahren", GEOPOLITICS: "geopolitischer Verlauf",
    SOURCE_FETCH_FAILED: "Quellenabruf fehlgeschlagen", UNKNOWN: "noch nicht bestätigt",
  };
  return String(value || "").replace(/\b(?:current_state|macro_inputs|meeting|policy_decision|resolution|path_step|house_vote|senate_vote|DIRECT_RESOLUTION|PATH_STEP|QUANTITATIVE_SIGNAL|CURRENT_STATE|MACRO_INPUTS|MEETING|POLICY_DECISION|RESOLUTION|LEGISLATION|GEOPOLITICS|SOURCE_FETCH_FAILED|UNKNOWN)\b/g, (token) => replacements[token] || token);
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
  const hasEvidence = ie && ie.available;
  const noEvidenceMessage = ie
    ? `Keine unabhängige Schätzung möglich — ${ie.detail}`
    : "Keine unabhängige Schätzung möglich — keine unabhängige Evidenz-Infrastruktur verfügbar.";

  const sourceCount = ie ? (ie.evidence_for_yes.length + ie.evidence_for_no.length) : 0;
  const sourceSummary = ie
    ? `${ie.evidence_for_yes.length} YES / ${ie.evidence_for_no.length} NO`
    : "keine verknüpften Quellen";

  // Real, already-found sources must stay visible even when the estimate
  // itself is unavailable (e.g. exactly 1 relevant article for Hormuz) —
  // "keine belastbare Prognose" must never mean "nothing to show".
  const foundButInsufficient = ie
    ? (ie.evidence_for_yes.length + ie.evidence_for_no.length + (ie.discarded_evidence || []).length) > 0
    : false;
  const evidenceCards = hasEvidence
    ? _sourceCardsHtml(ie, p)
    : foundButInsufficient
      ? `<div class="empty-state">${noEvidenceMessage}</div>${_sourceCardsHtml(ie, p)}`
      : `<div class="empty-state">${noEvidenceMessage}</div>`;

  const advancedDetails = `
    ${_resolutionEdgeHtml(p.resolution_edge)}
    ${_crossMarketHtml(p.cross_market)}
    ${_reactionLagHtml(p.reaction_lag)}
    ${_marketFlowHtml(p)}
    ${_worldStateHtml(p)}
    ${_dataGapsHtml(p)}
    ${_historicalComparablesHtml(p)}
    ${_divergenceAuditHtml(p)}
  `;

  return `
    <h3>Belege & Quellen</h3>
    <div class="widget-grid">
      ${widgetCard({ title: "Quellenlage", value: sourceSummary })}
      ${widgetCard({ title: "Quellenqualität", value: ie && ie.source_quality_score !== null ? `${fmtNum(ie.source_quality_score, 0)} / 100` : "–" })}
      ${widgetCard({ title: "Verifizierte Quellen", value: sourceCount })}
      ${widgetCard({ title: "Erste Meldung", value: ie && ie.time_since_first_report_hours !== null ? `${fmtNum(ie.time_since_first_report_hours, 1)} h` : "–" })}
      ${widgetCard({ title: "Divergenz", value: ie && ie.divergence !== null ? fmtEdgePp(ie.divergence) : "–" })}
    </div>
    ${evidenceCards}
    <details>
      <summary>Erweiterte Forschung & Audit</summary>
      ${hasEvidence ? _detailedEvidenceHtml(p, ie) : ""}
      ${advancedDetails}
      <div id="research-run-panel"><div class="empty-state">Lade Research-Verlauf…</div></div>
    </details>
  `;
}

// Priority 10: compact, real Research-Run history in the Advanced/Audit
// area only -- never prominent, never a raw developer-data dump. Shows
// the single most recent real research_runs row for this market.
async function _loadResearchRunPanel(marketId) {
  const el = document.getElementById("research-run-panel");
  if (!el) return;
  try {
    const runs = await Api.researchRuns(marketId, 1);
    if (!runs.length) {
      el.innerHTML = `<h4>Letzte Recherche</h4><div class="empty-state">Noch kein Research-Lauf für diesen Markt.</div>`;
      return;
    }
    const r = runs[0];
    let detail = {};
    try { detail = JSON.parse(r.detail_json || "{}"); } catch { /* ignore */ }
    const sourceFetchStatus = detail.source_fetch_status || "–";
    el.innerHTML = `
      <h4>Letzte Recherche (${fmtDate(r.run_at)})</h4>
      <div class="widget-grid">
        ${widgetCard({ title: "Quellen angefragt", value: r.sources_requested })}
        ${widgetCard({ title: "Quellen akzeptiert", value: r.sources_accepted })}
        ${widgetCard({ title: "Claims gefunden", value: r.claims_extracted })}
        ${widgetCard({ title: "Data Gaps vorher/nachher", value: `${r.data_gaps_before ?? "–"} → ${r.data_gaps_after ?? "–"}` })}
        ${widgetCard({ title: "Forecast vorher/nachher", value: `${r.published_forecast_before !== null ? fmtPct(r.published_forecast_before) : "–"} → ${r.published_forecast_after !== null ? fmtPct(r.published_forecast_after) : "–"}` })}
        ${widgetCard({ title: "Source Fetch Status", value: sourceFetchStatus })}
        ${widgetCard({ title: "Ergebnis", value: r.final_status || "–" })}
      </div>
    `;
  } catch {
    el.innerHTML = `<h4>Letzte Recherche</h4><div class="empty-state">Nicht verfügbar.</div>`;
  }
}

function _sourceCardsHtml(ie, p) {
  const items = [
    ...(ie.evidence_for_yes || []).slice(0, 3).map((item) => ({ item, direction: "YES" })),
    ...(ie.evidence_for_no || []).slice(0, 3).map((item) => ({ item, direction: "NO" })),
  ];
  if (!items.length) {
    return `<div class="empty-state">${_hasQuantitativeSupport(p) ? "Keine passenden Nachrichtenquellen gefunden. Die vorhandene Einschätzung beruht auf quantitativen Daten, nicht auf Nachrichtenbelegen." : "Keine zentralen, marktrelevanten Quellen gefunden."}</div>`;
  }
  return `
    <div class="source-card-grid">
      ${items.map(({ item, direction }) => `
        <div class="source-card">
          <div class="source-card-title"><a href="${item.url}" target="_blank" rel="noopener">${item.title}</a></div>
          <div class="sub">${item.source_domain || item.source} · ${fmtDate(item.published_at)}</div>
          <div><strong>${direction}</strong> · ${item.relation_label}</div>
          <div class="sub">${item.entailment} · Qualität ${fmtNum(item.reliability * 100, 0)}%</div>
        </div>
      `).join("")}
    </div>
  `;
}

function _detailedEvidenceHtml(p, ie) {
  const tableFor = (items) => {
    if (!items || !items.length) return `<p class="sub">keine</p>`;
    const rows = items.map((e) => `
      <tr>
        <td><a href="${e.url}" target="_blank" rel="noopener">${e.title}</a><div class="sub">${e.source_domain || e.source} · ${fmtDate(e.published_at)}</div></td>
        <td>${e.relation_label}</td>
        <td>${e.entailment}</td>
        <td>${(e.link_confidence * 100).toFixed(0)}%</td>
        <td>${(e.reliability * 100).toFixed(0)}%</td>
        <td>${(e.recency_weight * 100).toFixed(0)}%</td>
        <td>${(e.relation_weight * 100).toFixed(0)}%</td>
      </tr>
    `).join("");
    return `<table><thead><tr><th>Quelle</th><th>Relation</th><th>Richtung</th><th>Relevanz</th><th>Qualität</th><th>Aktualität</th><th>Impact</th></tr></thead><tbody>${rows}</tbody></table>`;
  };

  return `
    <h4>Unabhängige Evidenz im Detail</h4>
    <p><strong>YES:</strong></p>${tableFor(ie.evidence_for_yes)}
    <p><strong>NO:</strong></p>${tableFor(ie.evidence_for_no)}
    ${ie.not_yet_priced_in && ie.not_yet_priced_in.length ? `<p><strong>Noch nicht eingepreist:</strong></p>${tableFor(ie.not_yet_priced_in)}` : ""}
    ${ie.discarded_evidence && ie.discarded_evidence.length ? `<details><summary>Verworfen / nicht verwendet (${ie.discarded_evidence.length})</summary>${tableFor(ie.discarded_evidence)}</details>` : ""}
    <p class="sub">${ie.detail}</p>
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

// Part 1.9 Forecast History: real prediction_snapshots rows (four-tier
// forecast-semantics fields) over time. Simple table — no new charting
// dependency, per the task's explicit "lightweight" constraint.
function _forecastHistorySparkline(rows) {
  const vals = rows.map((r) => r.published_forecast_probability).filter((v) => v !== null && v !== undefined);
  if (vals.length < 2) return "";
  const w = 300, h = 40;
  const pts = rows.map((r, i) => {
    const v = r.published_forecast_probability;
    const x = (i / (rows.length - 1)) * w;
    const y = v === null || v === undefined ? null : h - v * h;
    return { x, y };
  }).filter((pt) => pt.y !== null);
  const path = pts.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="40" preserveAspectRatio="none"><path d="${path}" fill="none" stroke="#2fd67f" stroke-width="2"/></svg>`;
}

function _forecastHistoryHtml(rows) {
  if (!rows || !rows.length) {
    return `<h3>Forecast-Verlauf</h3><div class="empty-state">Noch keine gespeicherten Prognose-Snapshots für diesen Markt.</div>`;
  }
  const recent = rows.slice(-20);
  const rowsHtml = recent.slice().reverse().map((r) => `
    <tr>
      <td>${fmtDate(r.created_at)}</td>
      <td>${fmtPct(r.market_probability)}</td>
      <td>${r.model_hypothesis_probability !== null && r.model_hypothesis_probability !== undefined ? fmtPct(r.model_hypothesis_probability) : "–"}</td>
      <td>${r.evidence_backed_probability !== null && r.evidence_backed_probability !== undefined ? fmtPct(r.evidence_backed_probability) : "–"}</td>
      <td>${r.published_forecast_probability !== null && r.published_forecast_probability !== undefined ? fmtPct(r.published_forecast_probability) : "–"}</td>
    </tr>
  `).join("");
  return `
    <h3>Forecast-Verlauf <span class="sub">(${rows.length} Snapshot(s))</span></h3>
    ${_forecastHistorySparkline(recent)}
    <details>
      <summary>Alle gespeicherten Snapshots anzeigen</summary>
    <table>
      <thead><tr><th>Zeitpunkt</th><th>Markt</th><th>Modellhypothese</th><th>Evidenzgestützt</th><th>Veröffentlicht</th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
    </details>
  `;
}

// Part 1.10 Historical reliability: Block G's real category-level
// Brier/log-loss evaluation, filtered to this market's category. Honestly
// shows "not enough resolved cases" — the correct state given known data
// scarcity — rather than fabricating a track record.
function _historicalReliabilityHtml(evalData, category) {
  if (!evalData) {
    return `<h3>Historische Zuverlässigkeit</h3><div class="empty-state">Evaluation nicht verfügbar.</div>`;
  }
  const catKey = category || "UNCATEGORIZED";
  const slice = (evalData.by_category || []).find((c) => c.key === catKey);
  if (!slice || slice.n === 0) {
    return `<h3>Historische Zuverlässigkeit</h3><div class="empty-state">Keine aufgelösten Prognosen für Kategorie "${catKey}" vorhanden — noch nicht genug Fälle für eine Aussage.</div>`;
  }
  return `
    <h3>Historische Zuverlässigkeit <span class="sub">(Kategorie: ${catKey})</span></h3>
    ${slice.too_small_for_conclusion ? `<p class="sub">Nur ${slice.n} aufgelöste(r) Fall/Fälle — nicht genug für eine belastbare Aussage.</p>` : ""}
    <div class="widget-grid">
      ${widgetCard({ title: "Aufgelöste Fälle", value: String(slice.n) })}
      ${widgetCard({ title: "Brier-Score", value: slice.brier_score !== null ? fmtNum(slice.brier_score, 3) : "–" })}
      ${widgetCard({ title: "Log-Loss", value: slice.log_loss !== null ? fmtNum(slice.log_loss, 3) : "–" })}
      ${widgetCard({ title: "Ø vorhergesagt", value: slice.mean_predicted_probability !== null ? fmtPct(slice.mean_predicted_probability) : "–" })}
      ${widgetCard({ title: "Beobachtete Rate", value: slice.observed_frequency !== null ? fmtPct(slice.observed_frequency) : "–" })}
    </div>
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
  const whyPanel = document.getElementById("why-panel");
  const changeTriggersPanel = document.getElementById("change-triggers-panel");
  if (!panel) return;

  const paint = (response) => {
    headlinePanel.innerHTML = _headlinePanelHtml(market, market.opportunity, response.prediction);
    summaryPanel.innerHTML = _summaryPanelHtml(response.prediction);
    if (whyPanel) whyPanel.innerHTML = _whySectionHtml(response.prediction);
    if (breakdownPanel) breakdownPanel.innerHTML = _independentBreakdownHtml(response.prediction);
    if (evidencePanel) {
      evidencePanel.innerHTML = _evidenceSectionHtml(response.prediction);
      _loadResearchRunPanel(marketId);
    }
    if (changeTriggersPanel) changeTriggersPanel.innerHTML = _changeTriggersHtml(response.prediction);
    panel.innerHTML = _aiCardHtml(response);
    scenariosPanel.innerHTML = _scenarioSectionHtml(response.prediction.scenarios);
    const futureMapPanel = document.getElementById("future-map-panel");
    const sensitivityPanel = document.getElementById("sensitivity-panel");
    if (futureMapPanel) futureMapPanel.innerHTML = _futureMapHtml(response.prediction.scenario_tree);
    if (sensitivityPanel) sensitivityPanel.innerHTML = _sensitivityHtml(response.prediction.sensitivity_audit);
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
