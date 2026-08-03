const NAV_ITEMS = [
  { hash: "#/dashboard", icon: "🏠", label: "Dashboard" },
  { hash: "#/markets", icon: "📈", label: "Märkte" },
  { hash: "#/watchlist", icon: "⭐", label: "Watchlist" },
  { hash: "#/signals", icon: "🔥", label: "Chancen" },
  { hash: "#/research", icon: "🧠", label: "Research" },
  { hash: "#/news", icon: "📰", label: "News" },
  { hash: "#/calendar", icon: "📅", label: "Kalender" },
  { hash: "#/resolutions", icon: "✅", label: "Resolutionen" },
  { hash: "#/simulation", icon: "🧪", label: "Simulation" },
  { hash: "#/performance", icon: "💹", label: "Performance" },
  { hash: "#/stats", icon: "📊", label: "Statistik" },
  { hash: "#/analytics", icon: "📐", label: "Analytics" },
  { hash: "#/quality", icon: "🧹", label: "Data Quality" },
  { hash: "#/compare", icon: "⚖", label: "Provider-Vergleich" },
  { hash: "#/providers", icon: "🔌", label: "Provider" },
  { hash: "#/monitoring", icon: "🛰", label: "Scanner-Monitoring" },
  { hash: "#/heatmap", icon: "🗺", label: "Heatmap" },
  { hash: "#/search", icon: "🔎", label: "Suche" },
  { hash: "#/settings", icon: "⚙", label: "Einstellungen" },
];

function renderSidebar(activeHash) {
  const el = document.getElementById("sidebar");
  const base = activeHash.split("?")[0].split("/").slice(0, 2).join("/");
  el.innerHTML =
    `<div class="brand">PolymarketPulse</div>` +
    NAV_ITEMS.map(
      (item) =>
        `<a href="${item.hash}" class="${item.hash === base ? "active" : ""}">` +
        `<span>${item.icon}</span><span>${item.label}</span></a>`
    ).join("");
}
