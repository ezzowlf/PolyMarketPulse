let _marketsState = {
  search: "", category: "", sort: "opportunity_score",
  minLiquidity: "", minVolume: "", minConfidence: "", minEdge: "", requirePrice: false,
};

function _marketRow(o) {
  return `
    <tr onclick="location.hash='#/market/${encodeURIComponent(o.market_id)}'" style="cursor:pointer">
      <td>${o.question}</td>
      <td>${o.market_yes_probability !== null ? fmtPct(o.market_yes_probability) : statusBadge("Preis fehlt")}</td>
      <td>${fmtPct(o.estimated_yes_probability)}</td>
      <td>${fmtEdgePp(o.net_yes_edge)}</td>
      <td>${fmtNum(o.confidence_score, 0)}</td>
      <td>${fmtNum(o.data_quality_score, 0)}</td>
      <td>${fmtDeadline(o.deadline_hours)}</td>
      <td>${statusBadge(o.status)}</td>
      <td>${fmtDate(o.last_seen_at)}</td>
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
        <input id="f-confidence" type="number" placeholder="Min. Confidence" value="${_marketsState.minConfidence}" />
        <input id="f-edge" type="number" placeholder="Min. Edge (pp)" value="${_marketsState.minEdge}" />
        <label><input type="checkbox" id="f-require-price" ${_marketsState.requirePrice ? "checked" : ""} /> Nur mit Preis</label>
        <select id="f-sort">
          <option value="opportunity_score">Interessanteste zuerst</option>
          <option value="edge">Größte Edge</option>
          <option value="confidence">Höchste Confidence</option>
          <option value="deadline">Nächste Deadline</option>
          <option value="liquidity">Höchste Liquidität</option>
          <option value="volume">Höchstes Volumen</option>
          <option value="last_seen">Zuletzt aktualisiert</option>
        </select>
        <button class="btn" id="f-apply">Filtern</button>
      </div>
      <div id="markets-table"><div class="empty-state">Lade Märkte…</div></div>
    </div>
  `;
  document.getElementById("f-sort").value = _marketsState.sort;

  async function load() {
    const tableEl = document.getElementById("markets-table");
    tableEl.innerHTML = `<div class="empty-state">Lade Märkte…</div>`;
    try {
      const params = { sort: _marketsState.sort, require_price: _marketsState.requirePrice };
      if (_marketsState.category) params.category = _marketsState.category;
      if (_marketsState.minLiquidity) params.min_liquidity = _marketsState.minLiquidity;
      if (_marketsState.minConfidence) params.min_confidence = _marketsState.minConfidence;
      if (_marketsState.minEdge) params.min_edge = Number(_marketsState.minEdge) / 100;
      let items = await Api.opportunities(params);
      if (_marketsState.search) {
        const term = _marketsState.search.toLowerCase();
        items = items.filter((o) => o.question.toLowerCase().includes(term));
      }
      if (_marketsState.minVolume) {
        items = items.filter((o) => (o.volume_24h || 0) >= Number(_marketsState.minVolume));
      }

      tableEl.innerHTML = items.length
        ? `<table>
            <thead><tr><th>Frage</th><th>YES-Preis</th><th>Eigene Wahrscheinlichkeit</th><th>Edge</th><th>Confidence</th><th>Datenqualität</th><th>Deadline</th><th>Status</th><th>Aktualisiert</th></tr></thead>
            <tbody>${items.map(_marketRow).join("")}</tbody>
          </table>`
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
      minConfidence: document.getElementById("f-confidence").value,
      minEdge: document.getElementById("f-edge").value,
      requirePrice: document.getElementById("f-require-price").checked,
      sort: document.getElementById("f-sort").value,
    };
    load();
  };

  await load();
}
