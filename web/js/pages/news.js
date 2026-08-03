async function renderNewsPage(container) {
  container.innerHTML = `<div class="empty-state">Lade News…</div>`;
  const items = await Api.news({ limit: 100 });
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state">Keine News gespeichert. News-Modul ist standardmäßig deaktiviert (POLYMARKETPULSE_NEWS_ENABLED) — mit \`polymarketpulse news-fetch\` abrufen.</div>`;
    return;
  }
  container.innerHTML = `
    <div class="panel">
      ${items
        .map(
          (n) => `
        <div style="padding:10px 0;border-bottom:1px solid var(--border)">
          <div style="font-weight:600">${n.title}</div>
          <div class="sub" style="color:var(--text-dim);font-size:12px">${n.source} · ${fmtDate(n.published_at)}</div>
        </div>`
        )
        .join("")}
    </div>
  `;
}
