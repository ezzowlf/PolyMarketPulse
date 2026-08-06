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
  const status = opp ? opp.status : "Datenlage unzureichend";
  return `
    <h2 style="margin:0 0 4px">${market.question}</h2>
    <div class="widget-grid">
      ${widgetCard({ title: "Markt glaubt", value: fmtPct(p.market_yes_probability) })}
      ${widgetCard({ title: "Unsere Engine", value: fmtPct(p.estimated_yes_probability) })}
      ${widgetCard({ title: "Differenz (Edge)", value: fmtEdgePp(p.net_yes_edge) })}
      ${widgetCard({ title: "Confidence", value: p.confidence_score !== undefined ? `${fmtNum(p.confidence_score, 1)} / 100` : "–" })}
      ${widgetCard({ title: "Status", value: statusBadge(status) })}
      ${widgetCard({ title: "Deadline", value: opp ? fmtDeadline(opp.deadline_hours) : "–" })}
    </div>
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

  return `
    <h3>Datenqualität: ${level}</h3>
    <ul>${reasons.map((r) => `<li>${r}</li>`).join("")}</ul>
    <details>
      <summary>Details (technische Aufschlüsselung)</summary>
      <table>
        <thead><tr><th>Vollständigkeit</th><th>Aktualität</th><th>Quellenübereinstimmung</th><th>Historische Fallzahl</th><th>Resolution-Klarheit</th><th>Liquidität</th></tr></thead>
        <tbody><tr>
          <td>${fmtNum(dq.vollstaendigkeit, 0)}</td><td>${fmtNum(dq.aktualitaet, 0)}</td><td>${fmtNum(dq.quellenuebereinstimmung, 0)}</td>
          <td>${fmtNum(dq.historische_fallzahl, 0)}</td><td>${fmtNum(dq.resolution_klarheit, 0)}</td><td>${fmtNum(dq.liquiditaet, 0)}</td>
        </tr></tbody>
      </table>
    </details>
  `;
}

function _evidenceSectionHtml(p) {
  const ie = p && p.independent_evidence;
  if (!ie) {
    return `<h3>Unabhängige Evidenz</h3><div class="empty-state">Keine unabhängige Schätzung möglich — keine unabhängige Evidenz-Infrastruktur verfügbar.</div>`;
  }
  if (!ie.available) {
    return `<h3>Unabhängige Evidenz</h3><div class="empty-state">keine unabhängige Schätzung möglich — ${ie.detail}</div>`;
  }
  const marketPct = fmtPct(p.market_yes_probability);
  const independentPct = fmtPct(ie.independent_yes_probability);
  const divergenceStr = ie.divergence !== null ? fmtEdgePp(ie.divergence) : "–";
  const evidenceList = (items) =>
    items && items.length
      ? `<ul>${items.map((e) => `<li>${fmtDate(e.published_at)} — <a href="${e.url}" target="_blank" rel="noopener">${e.title}</a> <span class="sub">(${e.source_domain || e.source})</span></li>`).join("")}</ul>`
      : `<p class="sub">keine</p>`;

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
    ${evidenceList(ie.evidence_for_yes)}
    <p><strong>Spricht für NO (${ie.evidence_for_no.length}):</strong></p>
    ${evidenceList(ie.evidence_for_no)}
    ${
      ie.not_yet_priced_in.length
        ? `<p><strong>Noch nicht eingepreist:</strong></p>${evidenceList(ie.not_yet_priced_in)}`
        : ""
    }
    <p class="sub">${ie.detail}</p>
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
  if (!panel) return;

  const paint = (response) => {
    headlinePanel.innerHTML = _headlinePanelHtml(market, market.opportunity, response.prediction);
    summaryPanel.innerHTML = _summaryPanelHtml(response.prediction);
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
