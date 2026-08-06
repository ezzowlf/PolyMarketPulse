function _watchlistRow(i) {
  const o = i.opportunity;
  const marketId = o ? o.market_id : null;
  const link = marketId ? `<a href="#/market/${encodeURIComponent(marketId)}">${i.question || i.provider_market_id}</a>` : (i.question || i.provider_market_id);
  return `
    <tr>
      <td>${link}</td>
      <td>${o ? fmtPct(o.market_yes_probability) : "–"}</td>
      <td>${o ? fmtPct(o.estimated_yes_probability) : "–"}</td>
      <td>${o ? fmtEdgePp(o.net_yes_edge) : "–"}</td>
      <td>${o ? fmtNum(o.confidence_score, 0) : "–"}</td>
      <td>${o ? fmtDeadline(o.deadline_hours) : "–"}</td>
      <td>${o ? statusBadge(o.status) : "–"}</td>
      <td>${i.note || "–"}</td>
      <td><button class="btn danger" data-id="${i.id}">Entfernen</button></td>
    </tr>
  `;
}

async function renderWatchlistPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Watchlist…</div>`;
  const items = await Api.watchlist();
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state">Watchlist ist leer. Füge Märkte über die Marktdetailseite hinzu.</div>`;
    return;
  }
  container.innerHTML = `
    <div class="disclaimer">Die Watchlist zeigt live berechnete Werte der eigenen Engine — keine automatische Wettausführung.</div>
    <div class="panel">
      <table>
        <thead><tr><th>Markt</th><th>Marktpreis</th><th>Eigene Prognose</th><th>Edge</th><th>Confidence</th><th>Deadline</th><th>Status</th><th>Notiz</th><th></th></tr></thead>
        <tbody>${items.map(_watchlistRow).join("")}</tbody>
      </table>
    </div>
  `;
  container.querySelectorAll("button[data-id]").forEach((btn) => {
    btn.onclick = async () => {
      await Api.removeWatchlist(btn.dataset.id);
      renderWatchlistPage(container);
    };
  });
}
