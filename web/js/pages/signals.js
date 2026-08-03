async function renderSignalsPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Signale…</div>`;
  const signals = await Api.signals({ limit: 100 });
  if (signals.length === 0) {
    container.innerHTML = `<div class="empty-state">Noch keine Research-Signale gespeichert. Führe zuerst einen Scan aus (\`polymarketpulse scan\`).</div>`;
    return;
  }
  container.innerHTML = `
    <div class="disclaimer">Research-Signale sind beobachtbare Marktereignisse, keine Kauf- oder Wettempfehlung.</div>
    <div class="panel">
      <table>
        <thead><tr><th>Zeit</th><th>Typ</th><th>Provider</th><th>Score</th><th>Markt</th><th>Status</th></tr></thead>
        <tbody>
          ${signals
            .map(
              (s) => `
            <tr class="clickable" data-id="${s.market_id || ""}">
              <td>${fmtDate(s.captured_at)}</td>
              <td><span class="badge">${s.signal_type}</span></td>
              <td>${s.provider}</td>
              <td>${fmtNum(s.score, 1)}</td>
              <td>${s.question || s.provider_market_id}</td>
              <td>${s.status}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
  container.querySelectorAll("tr.clickable").forEach((row) => {
    row.onclick = () => {
      window.location.hash = `#/market/${encodeURIComponent(row.dataset.id)}`;
    };
  });
}
