const ROUTES = {
  dashboard: { title: "Startseite", render: renderDashboardPage },
  markets: { title: "Märkte", render: renderMarketsPage },
  watchlist: { title: "Beobachtungsliste", render: renderWatchlistPage },
  signals: { title: "Shadow-Setups", render: renderSignalsPage },
  news: { title: "Nachrichten", render: renderNewsPage },
  calendar: { title: "Kalender", render: renderCalendarPage },
  stats: { title: "Statistik", render: renderStatsPage },
  heatmap: { title: "Heatmap", render: renderHeatmapPage },
  settings: { title: "Einstellungen", render: renderSettingsPage },
  quality: { title: "Datenqualität", render: renderQualityPage },
  performance: { title: "Auswertung", render: renderPerformancePage },
  simulation: { title: "Virtuelle Entwicklung", render: renderPerformancePage },
  resolutions: { title: "Entscheidungen", render: renderResolutionsPage },
  compare: { title: "Plattform-Vergleich", render: renderComparePage },
  providers: { title: "Datenquellen", render: renderProviderStatusPage },
  monitoring: { title: "Scanner-Status", render: renderMonitoringPage },
  research: { title: "KI-Analyse", render: renderResearchPage },
  search: { title: "Suche", render: renderSearchPage },
  analytics: { title: "Statistiken (erweitert)", render: renderAnalyticsPage },
  backtest: { title: "Backtest & KI-Kosten", render: renderBacktestPage },
};

async function router() {
  const hash = window.location.hash || "#/dashboard";
  renderSidebar(hash);

  const [, path, query] = hash.match(/^#\/([^?]*)(?:\?(.*))?$/) || [null, "dashboard", ""];
  const parts = path.split("/");
  const content = document.getElementById("content");

  if (parts[0] === "market" && parts[1]) {
    document.getElementById("page-title").textContent = "Marktdetail";
    await renderMarketDetailPage(content, decodeURIComponent(parts[1]));
    return;
  }

  const route = ROUTES[parts[0]] || ROUTES.dashboard;
  document.getElementById("page-title").textContent = route.title;
  try {
    await route.render(content, query);
  } catch (err) {
    content.innerHTML = `<div class="empty-state">Unerwarteter Fehler: ${err.message}</div>`;
    console.error(err);
  }
}

async function checkApiHealth() {
  const statusEl = document.getElementById("api-status");
  try {
    const health = await Api.health();
    statusEl.textContent = `API: ${health.status}`;
    statusEl.className = "topbar-status ok";
  } catch {
    statusEl.textContent = "API: nicht erreichbar";
    statusEl.className = "topbar-status err";
  }
}

window.addEventListener("hashchange", router);
window.addEventListener("DOMContentLoaded", () => {
  checkApiHealth();
  router();
  setInterval(checkApiHealth, 30000);
});
