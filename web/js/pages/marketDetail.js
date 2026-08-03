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
          ${widgetCard({ title: "Provider", value: market.provider })}
          ${widgetCard({ title: "YES-Preis", value: fmtPct(latest.yes_price) })}
          ${widgetCard({ title: "Liquidität", value: "$" + fmtNum(latest.liquidity) })}
          ${widgetCard({ title: "24h-Volumen", value: "$" + fmtNum(latest.volume_24h) })}
          ${widgetCard({ title: "Spread", value: fmtPct(latest.spread) })}
          ${widgetCard({ title: "Research-Score", value: fmtNum(latest.opportunity_score, 1) })}
          ${widgetCard({ title: "Auflösung", value: fmtDate(market.end_date) })}
          ${widgetCard({ title: "Status", value: market.resolution_status })}
        </div>
        <p><a href="${market.url}" target="_blank" rel="noopener">Zur Originalplattform →</a></p>
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
        <h3>News</h3>
        ${
          market.news.length
            ? `<ul>${market.news.map((n) => `<li>${n.title} — <span class="badge">${(n.confidence * 100).toFixed(0)}% Confidence</span></li>`).join("")}</ul>`
            : `<div class="empty-state">Keine verknüpften News.</div>`
        }
      </div>

      <button class="btn" id="add-watchlist">Zur Watchlist hinzufügen</button>
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
      alert("Zur Watchlist hinzugefügt.");
    };
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}
