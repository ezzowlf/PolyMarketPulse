let _marketsState = {
  search: "", category: "", sort: "opportunity_score",
  minLiquidity: "", minVolume: "",
};

// Block H Part 2: compact Markets-list row — question / market% / PMP%
// (or "–", never model_hypothesis_probability) / status / data-situation /
// category / deadline. Consumes the real /markets endpoint (Block G Part 5
// already joins the latest prediction_snapshots row server-side).
function _dataSituationLabel(dqScore) {
  if (dqScore === null || dqScore === undefined) return "–";
  if (dqScore >= 75) return "Gut";
  if (dqScore >= 45) return "Mittel";
  return "Schwach";
}

function _marketRow(m) {
  const pmp = m.published_forecast_probability !== null && m.published_forecast_probability !== undefined
    ? fmtPct(m.published_forecast_probability)
    : "–";
  const statusLabel = (typeof FORECAST_STATUS_LABEL_DE !== "undefined" && FORECAST_STATUS_LABEL_DE[m.forecast_status])
    || m.forecast_status || "–";
  return `
    <tr onclick="location.hash='#/market/${encodeURIComponent(m.market_id)}'" style="cursor:pointer">
      <td>${m.question}</td>
      <td>${m.yes_price !== null && m.yes_price !== undefined ? fmtPct(m.yes_price) : statusBadge("Preis fehlt")}</td>
      <td>${pmp}</td>
      <td>${statusLabel}</td>
      <td>${_dataSituationLabel(m.data_quality_composite_score)}</td>
      <td class="sub">${m.category || "–"}</td>
      <td>${fmtDate(m.end_date)}</td>
    </tr>
  `;
}

async function renderMarketsPage(container) {
  container.innerHTML = `
    <div class="panel">
      <div class="filters">
        <input id="f-search" placeholder="Suche nach Frage…" value="${_marketsState.search}" />
        <input id="f-category" placeholder="Kategorie" value="${_marketsState.category}" />
        <input id="f-liquidity" type="number" placeholder="Min. Liquidität" value="${_marketsState.minLiquidity}" />
        <input id="f-volume" type="number" placeholder="Min. Volumen" value="${_marketsState.minVolume}" />
        <button class="btn" id="f-apply">Filtern</button>
      </div>
      <div id="markets-table"><div class="empty-state">Lade Märkte…</div></div>
    </div>
  `;

  async function load() {
    const tableEl = document.getElementById("markets-table");
    tableEl.innerHTML = `<div class="empty-state">Lade Märkte…</div>`;
    try {
      const params = { limit: 200 };
      if (_marketsState.category) params.category = _marketsState.category;
      if (_marketsState.search) params.search = _marketsState.search;
      if (_marketsState.minLiquidity) params.min_liquidity = _marketsState.minLiquidity;
      if (_marketsState.minVolume) params.min_volume = _marketsState.minVolume;
      const result = await Api.markets(params);
      const items = result.items || [];

      tableEl.innerHTML = items.length
        ? `<table>
            <thead><tr><th>Frage</th><th>Markt%</th><th>Prognose (PMP%)</th><th>Status</th><th>Datenlage</th><th>Kategorie</th><th>Deadline</th></tr></thead>
            <tbody>${items.map(_marketRow).join("")}</tbody>
          </table>
          <p class="sub">${items.length} von ${result.total} Märkten.</p>`
        : `<div class="empty-state">Keine Märkte gefunden.</div>`;
    } catch (err) {
      tableEl.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
    }
  }

  document.getElementById("f-apply").onclick = () => {
    _marketsState = {
      search: document.getElementById("f-search").value,
      category: document.getElementById("f-category").value,
      minLiquidity: document.getElementById("f-liquidity").value,
      minVolume: document.getElementById("f-volume").value,
      sort: _marketsState.sort,
    };
    load();
  };

  await load();
}
