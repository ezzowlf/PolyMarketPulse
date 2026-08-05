const NAV_ITEMS = [
  { hash: "#/dashboard", icon: "🏠", label: "Startseite" },
  { hash: "#/markets", icon: "📈", label: "Märkte" },
  { hash: "#/watchlist", icon: "⭐", label: "Beobachtungsliste" },
  { hash: "#/signals", icon: "🌗", label: "Shadow-Setups" },
  { hash: "#/research", icon: "🧠", label: "KI-Analyse" },
  { hash: "#/news", icon: "📰", label: "Nachrichten" },
  { hash: "#/calendar", icon: "📅", label: "Kalender" },
  { hash: "#/resolutions", icon: "✅", label: "Entscheidungen" },
  { hash: "#/simulation", icon: "🧪", label: "Virtuelle Entwicklung" },
  { hash: "#/performance", icon: "💹", label: "Auswertung" },
  { hash: "#/stats", icon: "📊", label: "Statistik" },
  { hash: "#/analytics", icon: "📐", label: "Statistiken (erweitert)" },
  { hash: "#/backtest", icon: "🧮", label: "Backtest & KI-Kosten" },
  { hash: "#/quality", icon: "🧹", label: "Datenqualität" },
  { hash: "#/compare", icon: "⚖", label: "Plattform-Vergleich" },
  { hash: "#/providers", icon: "🔌", label: "Datenquellen" },
  { hash: "#/monitoring", icon: "🛰", label: "Scanner-Status" },
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
