async function renderResolutionsPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Resolutionen…</div>`;
  const rows = await Api.resolutions();
  if (rows.length === 0) {
    container.innerHTML = `<div class="empty-state">Keine Resolutionen erfasst. \`polymarketpulse resolutions --provider polymarket\` ausführen.</div>`;
    return;
  }
  const statusBadge = (s) =>
    ({ resolved: "green", cancelled: "yellow", invalid: "yellow", disputed: "red" }[s] || "");

  container.innerHTML = `
    <div class="panel">
      <table>
        <thead><tr><th>Markt</th><th>Provider</th><th>Status</th><th>Gewinner</th><th>Aufgelöst</th><th>Quelle</th></tr></thead>
        <tbody>
          ${rows
            .map(
              (r) => `
            <tr class="clickable" data-provider="${r.provider}" data-id="${r.provider_market_id}">
              <td>${r.question || r.provider_market_id}</td>
              <td><span class="badge">${r.provider}</span></td>
              <td><span class="badge ${statusBadge(r.status)}">${r.status}</span></td>
              <td>${r.winning_outcome || "–"}</td>
              <td>${fmtDate(r.resolved_at)}</td>
              <td>${r.resolution_source || "–"}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}
