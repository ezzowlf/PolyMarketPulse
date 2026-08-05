async function renderMarketDetailPage(container, marketId) {
  container.innerHTML = `<div class="empty-state">Lade Markt…</div>`;
  try {
    const [market, historyFull] = await Promise.all([Api.market(marketId), Api.historyFull(marketId)]);
    const history = historyFull.points;
    const a = historyFull.analytics;
    const latest = market.latest || {};

    container.innerHTML = `
      <div class="disclaimer">Research-Hinweis – keine Wettaufforderung, kein sicherer Gewinn. Alle Werte aus der Datenbank, keine künstlichen Berechnungen.</div>
      <div class="panel">
        <h2 style="margin-top:0">${market.question}</h2>
        <p style="color:var(--text-dim)">${market.description || ""}</p>
        <div class="widget-grid">
          ${widgetCard({ title: "Datenquelle", value: market.provider })}
          ${widgetCard({ title: "YES-Preis", value: fmtPct(latest.yes_price) })}
          ${widgetCard({ title: "Liquidität", value: "$" + fmtNum(latest.liquidity) })}
          ${widgetCard({ title: "24h-Volumen", value: "$" + fmtNum(latest.volume_24h) })}
          ${widgetCard({ title: "Spread", value: fmtPct(latest.spread) })}
          ${widgetCard({ title: "Research-Score (Priorität, 0–100)", value: fmtNum(latest.opportunity_score, 1), sub: "Ranking-Wert, keine Wahrscheinlichkeit" })}
          ${widgetCard({ title: "Auflösung", value: fmtDate(market.end_date) })}
          ${widgetCard({ title: "Marktstatus", value: fmtStatus(market.resolution_status) })}
        </div>
        <p><a href="${market.url}" target="_blank" rel="noopener">Zur Originalplattform →</a></p>
      </div>

      <div class="panel" id="prediction-panel">
        <h3>Eigene Prognose &amp; KI-Erklärung</h3>
        <div class="empty-state">Lade Prognose…</div>
      </div>

      <div class="panel">
        <h3>Historische Analyse</h3>
        <div class="widget-grid">
          ${widgetCard({ title: "Preisänderung", value: a.price_change !== null ? fmtNum(a.price_change, 3) : "–", sub: a.price_change_pct !== null ? fmtPct(a.price_change_pct) : "" })}
          ${widgetCard({ title: "Gleitender Ø (kurz/lang)", value: `${a.moving_average_short !== null ? fmtNum(a.moving_average_short, 3) : "–"} / ${a.moving_average_long !== null ? fmtNum(a.moving_average_long, 3) : "–"}` })}
          ${widgetCard({ title: "Volatilität", value: a.volatility !== null ? fmtNum(a.volatility, 4) : "–" })}
          ${widgetCard({ title: "Max. Einzelbewegung", value: a.max_price_change !== null ? fmtNum(a.max_price_change, 3) : "–" })}
          ${widgetCard({ title: "Trendwechsel", value: a.trend_reversals })}
          ${widgetCard({ title: "Liquiditätstrend", value: a.liquidity_trend })}
        </div>
        <p><a href="#/research?market=${encodeURIComponent(marketId)}">🧠 Warum bewegt sich dieser Markt? (Research-Analyse) →</a></p>
      </div>

      <div class="panel">
        <h3>Preisverlauf (YES)</h3>
        <canvas class="chart-canvas" id="price-chart"></canvas>
      </div>

      <div class="panel">
        <h3>Research-Score-Verlauf</h3>
        <canvas class="chart-canvas" id="score-chart"></canvas>
      </div>

      <div class="panel">
        <h3>Signalhistorie</h3>
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

      <div class="panel">
        <h3>Nachrichten</h3>
        ${
          market.news.length
            ? `<ul>${market.news.map((n) => `<li>${n.title} — <span class="badge">${(n.confidence * 100).toFixed(0)}% Relevanz</span></li>`).join("")}</ul>`
            : `<div class="empty-state">Keine verknüpften Nachrichten.</div>`
        }
      </div>

      <button class="btn" id="add-watchlist">Zur Beobachtungsliste hinzufügen</button>
    `;

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
      alert("Zur Beobachtungsliste hinzugefügt.");
    };

    renderPredictionPanel(marketId);
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}

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

function _submodelSectionHtml(p) {
  const submodels = p.submodel_estimates || [];
  if (!submodels.length) return "";
  return `
    <h4>Ensemble — unabhängige Teilmodelle (Deadline-Phase: ${DEADLINE_PHASE_LABEL_DE[p.deadline_phase] || p.deadline_phase})</h4>
    <table>
      <thead><tr><th>Modell</th><th>Verfügbar</th><th>Schätzung YES</th><th>Ensemble-Gewicht</th><th>Detail</th></tr></thead>
      <tbody>
        ${submodels
          .map(
            (s) =>
              `<tr><td>${s.name}</td><td>${s.available ? "ja" : "nein"}</td><td>${s.estimated_yes_probability !== null ? fmtPct(s.estimated_yes_probability) : "–"}</td><td>${fmtNum(s.weight, 2)}</td><td class="sub">${s.detail}</td></tr>`
          )
          .join("")}
      </tbody>
    </table>
    ${p.ensemble_agreement !== null && p.ensemble_agreement !== undefined ? `<p class="sub">Modellübereinstimmung (Agreement): ${fmtPct(p.ensemble_agreement)}</p>` : ""}
    ${p.news_sentiment_score !== null && p.news_sentiment_score !== undefined ? `<p class="sub">Gewichtete News-Stimmung: ${p.news_sentiment_score.toFixed(2)} (-1 negativ .. +1 positiv), ${p.news_confirmation_count} unabhängige Quelle(n)</p>` : ""}
  `;
}

function _scenarioSectionHtml(scenarios) {
  if (!scenarios) return "";
  return `
    <h4>Szenarien</h4>
    <p><strong>Basisszenario:</strong> ${scenarios.base_case}</p>
    ${scenarios.bull_case && scenarios.bull_case.length ? `<p><strong>Bull Case:</strong></p><ul>${scenarios.bull_case.map((s) => `<li>${s}</li>`).join("")}</ul>` : ""}
    ${scenarios.bear_case && scenarios.bear_case.length ? `<p><strong>Bear Case:</strong></p><ul>${scenarios.bear_case.map((s) => `<li>${s}</li>`).join("")}</ul>` : ""}
  `;
}

function _predictionPanelHtml(response) {
  const p = response.prediction;
  const e = response.explanation;
  const meta = response.meta;
  const dq = p.data_quality_breakdown || {};
  const cost = meta.actual_cost_usd ?? meta.estimated_cost_usd;

  return `
    <div class="disclaimer">
      Die statistische Prognose (Markt-/Modellwahrscheinlichkeit, Edge, Empfehlung) wird ausschließlich von der
      eigenen Engine berechnet. GPT-5 nano erklärt diese Werte nur — es erfindet und verändert keine Wahrscheinlichkeit.
    </div>
    <div class="widget-grid">
      ${widgetCard({ title: "Marktpreis YES / NO", value: `${fmtPct(p.market_yes_probability)} / ${fmtPct(p.market_no_probability)}` })}
      ${widgetCard({ title: "Eigene Prognose YES / NO", value: `${fmtPct(p.estimated_yes_probability)} / ${fmtPct(p.estimated_no_probability)}` })}
      ${widgetCard({ title: "Brutto-Edge (YES)", value: p.gross_yes_edge !== null ? (p.gross_yes_edge * 100).toFixed(1) + " pp" : "–" })}
      ${widgetCard({ title: "Netto-Edge (nach Kosten/Spread)", value: p.net_yes_edge !== null ? (p.net_yes_edge * 100).toFixed(1) + " pp" : "–" })}
      ${widgetCard({ title: "Richtung", value: DIRECTION_LABEL_DE[e.direction] || e.direction })}
      ${widgetCard({ title: "Empfehlung", value: RECOMMENDATION_LABEL_DE[p.recommendation] || p.recommendation })}
      ${widgetCard({ title: "Modellvertrauen (0–100, kein Wahrscheinlichkeitswert)", value: fmtNum(p.confidence_score, 1) })}
      ${widgetCard({ title: "Datenqualität (0–100)", value: fmtNum(p.data_quality_score, 1) })}
      ${widgetCard({ title: "Unsicherheitsbereich (YES)", value: p.uncertainty_lower !== null ? `${fmtPct(p.uncertainty_lower)} – ${fmtPct(p.uncertainty_upper)}` : "–" })}
      ${widgetCard({ title: "Historische Vergleichsfälle", value: p.comparable_sample_size })}
    </div>

    <h4>Datenqualität — Aufschlüsselung</h4>
    <table>
      <thead><tr><th>Vollständigkeit</th><th>Aktualität</th><th>Quellenübereinstimmung</th><th>Historische Fallzahl</th><th>Resolution-Klarheit</th><th>Liquidität</th></tr></thead>
      <tbody><tr>
        <td>${fmtNum(dq.vollstaendigkeit, 0)}</td>
        <td>${fmtNum(dq.aktualitaet, 0)}</td>
        <td>${fmtNum(dq.quellenuebereinstimmung, 0)}</td>
        <td>${fmtNum(dq.historische_fallzahl, 0)}</td>
        <td>${fmtNum(dq.resolution_klarheit, 0)}</td>
        <td>${fmtNum(dq.liquiditaet, 0)}</td>
      </tr></tbody>
    </table>

    ${_submodelSectionHtml(p)}
    ${_scenarioSectionHtml(p.scenarios)}

    <h4>${e.headline}</h4>
    <p>${e.summary}</p>
    <p>${e.recommendation_explanation}</p>
    ${e.supports_yes && e.supports_yes.length ? `<p><strong>Spricht für YES:</strong></p><ul>${e.supports_yes.map((f) => `<li>[${f.impact}] ${f.factor}</li>`).join("")}</ul>` : ""}
    ${e.supports_no && e.supports_no.length ? `<p><strong>Spricht für NO:</strong></p><ul>${e.supports_no.map((f) => `<li>[${f.impact}] ${f.factor}</li>`).join("")}</ul>` : ""}
    ${e.uncertainties && e.uncertainties.length ? `<p><strong>Unsicherheiten:</strong> ${e.uncertainties.join("; ")}</p>` : ""}
    ${e.data_gaps && e.data_gaps.length ? `<p><strong>Datenlücken:</strong> ${e.data_gaps.join("; ")}</p>` : ""}
    <p><strong>Historischer Kontext:</strong> ${e.historical_context}</p>
    <p class="sub">${e.warning}</p>

    <div class="widget-grid">
      ${widgetCard({ title: "Modell", value: meta.model })}
      ${widgetCard({ title: "Kosten dieser Analyse", value: cost !== null && cost !== undefined ? "$" + Number(cost).toFixed(5) : "–" })}
      ${widgetCard({ title: "Fallback (regelbasiert statt KI)", value: meta.used_fallback ? "ja" : "nein", sub: meta.fallback_reason || "" })}
      ${widgetCard({ title: "Aus Cache", value: meta.cached ? "ja" : "nein" })}
      ${widgetCard({ title: "Letzte Aktualisierung", value: fmtDate(meta.created_at) })}
    </div>
    <button class="btn" id="recompute-prediction">Neu berechnen (Cache umgehen)</button>
  `;
}

async function renderPredictionPanel(marketId) {
  const panel = document.getElementById("prediction-panel");
  if (!panel) return;
  try {
    const response = await Api.explainRecommendation(marketId);
    panel.querySelector(".empty-state") ?? null;
    panel.innerHTML = `<h3>Eigene Prognose &amp; KI-Erklärung</h3>` + _predictionPanelHtml(response);
    document.getElementById("recompute-prediction").onclick = async () => {
      panel.innerHTML = `<h3>Eigene Prognose &amp; KI-Erklärung</h3><div class="empty-state">Berechne neu…</div>`;
      try {
        const fresh = await Api.explainRecommendationRecompute(marketId);
        panel.innerHTML = `<h3>Eigene Prognose &amp; KI-Erklärung</h3>` + _predictionPanelHtml(fresh);
        document.getElementById("recompute-prediction").onclick = () => renderPredictionPanel(marketId);
      } catch (err) {
        panel.innerHTML = `<h3>Eigene Prognose &amp; KI-Erklärung</h3><div class="empty-state">Fehler: ${err.message}</div>`;
      }
    };
  } catch (err) {
    panel.innerHTML = `<h3>Eigene Prognose &amp; KI-Erklärung</h3><div class="empty-state">Fehler: ${err.message}</div>`;
  }
}
