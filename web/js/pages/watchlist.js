async function renderWatchlistPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Beobachtungsliste…</div>`;
  const items = await Api.watchlist();
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state">Beobachtungsliste ist leer. Füge Märkte über die Marktdetailseite hinzu.</div>`;
    return;
  }
  container.innerHTML = `
    <div class="panel">
      <table>
        <thead><tr><th>Markt</th><th>Datenquelle</th><th>Notiz</th><th>Hinzugefügt</th><th></th></tr></thead>
        <tbody>
          ${items
            .map(
              (i) => `
            <tr>
              <td>${i.question || i.provider_market_id}</td>
              <td><span class="badge">${i.provider}</span></td>
              <td>${i.note || "–"}</td>
              <td>${fmtDate(i.created_at)}</td>
              <td><button class="btn danger" data-id="${i.id}">Entfernen</button></td>
            </tr>`
            )
            .join("")}
        </tbody>
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
