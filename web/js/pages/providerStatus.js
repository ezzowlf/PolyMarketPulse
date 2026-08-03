async function renderProviderStatusPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Provider-Status…</div>`;
  const rows = await Api.providersStatus();
  container.innerHTML = `
    <div class="widget-grid">
      ${rows
        .map((p) =>
          widgetCard({
            title: p.name,
            value: p.capabilities.market_lists ? "aktiv" : "nicht verfügbar",
            sub: `${fmtNum(p.markets_stored)} Märkte · ${fmtNum(p.resolutions_recorded)} Resolutionen`,
            listItems: [
              `Letzter Lauf: ${p.last_run ? p.last_run.status : "–"} (${p.last_run ? fmtDate(p.last_run.finished_at) : "–"})`,
              `Ø Laufzeit: ${p.average_run_duration_ms ? fmtNum(p.average_run_duration_ms) + " ms" : "–"}`,
              `Fehlgeschlagene Läufe: ${p.recent_failed_runs}`,
              `Echtgeld: ${p.capabilities.real_money ? "ja" : "nein"} · Auth nötig: ${p.capabilities.requires_auth ? "ja" : "nein"}`,
            ],
          })
        )
        .join("")}
    </div>
  `;
}
