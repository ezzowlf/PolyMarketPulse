async function renderDashboardPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Dashboard…</div>`;
  try {
    const [providers, markets, signals, stats, news, analytics, health] = await Promise.all([
      Api.providers(),
      Api.markets({ limit: 100 }),
      Api.signals({ limit: 10 }),
      Api.stats(),
      Api.news({ limit: 5 }),
      Api.analytics(),
      Api.health(),
    ]);

    const activeProviders = providers.filter((p) => p.market_lists).length;
    const upcoming = markets.items
      .filter((m) => m.end_date)
      .sort((a, b) => new Date(a.end_date) - new Date(b.end_date))
      .slice(0, 5);
    const movers = [...markets.items]
      .filter((m) => m.opportunity_score !== null)
      .sort((a, b) => (b.opportunity_score || 0) - (a.opportunity_score || 0))
      .slice(0, 5);
    const newMarkets = [...markets.items].slice(0, 5);

    container.innerHTML = `
      <div class="disclaimer">Research-Hinweis – keine Wettaufforderung. Alle Werte stammen ausschließlich aus lokal gespeicherten Daten, keine Live-Provider-Abfrage bei jedem Seitenaufruf.</div>
      <div class="widget-grid">
        ${widgetCard({
          title: "Research-Signale",
          value: fmtNum(stats.signal_count),
          sub: `${fmtNum(stats.evaluated_count)} ausgewertet`,
        })}
        ${widgetCard({
          title: "Aktive Märkte (gespeichert)",
          value: fmtNum(analytics.markets),
        })}
        ${widgetCard({
          title: "Provider-Status",
          listItems: providers.map(
            (p) => `${p.name} — <span class="badge ${p.market_lists ? "green" : "yellow"}">${p.market_lists ? "aktiv" : "nicht verfügbar"}</span>`
          ),
        })}
        ${widgetCard({
          title: "Neue Signale",
          listItems: signals.slice(0, 5).map((s) => `${s.signal_type} — ${s.question || s.provider_market_id}`),
        })}
        ${widgetCard({
          title: "Größte Bewegungen (Score)",
          listItems: movers.map((m) => `${fmtNum(m.opportunity_score, 1)} — ${m.question}`),
        })}
        ${widgetCard({
          title: "Neue Märkte",
          listItems: newMarkets.map((m) => m.question),
        })}
        ${widgetCard({
          title: "Bevorstehende Resolutionen",
          listItems: upcoming.map((m) => `${fmtDate(m.end_date)} — ${m.question}`),
        })}
        ${widgetCard({
          title: "News",
          listItems: news.map((n) => `${n.source}: ${n.title}`),
        })}
        ${widgetCard({
          title: "Datenbankstatus",
          sub: `Schema v${analytics.schema_version}`,
          listItems: [
            `Snapshots: ${fmtNum(analytics.market_snapshots)}`,
            `Signale: ${fmtNum(analytics.research_signals)}`,
            `Resolutionen: ${fmtNum(analytics.market_resolutions)}`,
          ],
        })}
        ${widgetCard({
          title: "Letzter Scan",
          value: analytics.last_run_status || "–",
          sub: `${analytics.last_run_provider || "–"} · ${fmtDate(analytics.last_run_finished_at)}`,
        })}
        ${widgetCard({
          title: "API-Status",
          value: health.status === "ok" ? "OK" : "Fehler",
        })}
      </div>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler beim Laden: ${err.message}</div>`;
  }
}
