async function renderMonitoringPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Scanner-Monitoring…</div>`;
  const [providerStatus, analytics] = await Promise.all([Api.providersStatus(), Api.analytics()]);

  container.innerHTML = `
    <div class="widget-grid">
      ${widgetCard({ title: "Scan-Läufe gesamt", value: fmtNum(analytics.scanner_runs) })}
      ${widgetCard({ title: "Letzter Lauf", value: analytics.last_run_status || "–", sub: `${analytics.last_run_provider || "–"} · ${fmtDate(analytics.last_run_finished_at)}` })}
      ${widgetCard({ title: "Schema-Version", value: analytics.schema_version })}
    </div>
    <div class="panel">
      <h3>Provider-Status im Detail</h3>
      <table>
        <thead><tr><th>Datenquelle</th><th>Letzter Status</th><th>Ø Laufzeit</th><th>Fehlgeschlagene Läufe</th><th>Letzter Fehler</th></tr></thead>
        <tbody>
          ${providerStatus
            .map(
              (p) => `
            <tr>
              <td>${p.name}</td>
              <td><span class="badge ${p.last_run && p.last_run.status === "completed" ? "green" : p.last_run && p.last_run.status === "failed" ? "red" : ""}">${p.last_run ? p.last_run.status : "–"}</span></td>
              <td>${p.average_run_duration_ms ? fmtNum(p.average_run_duration_ms) + " ms" : "–"}</td>
              <td>${p.recent_failed_runs}</td>
              <td>${p.last_run && p.last_run.error_details ? p.last_run.error_details : "–"}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}
