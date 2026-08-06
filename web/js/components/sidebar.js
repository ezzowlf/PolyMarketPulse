const MAIN_NAV_ITEMS = [
  { hash: "#/dashboard", icon: "🏠", label: "Übersicht" },
  { hash: "#/markets", icon: "📈", label: "Märkte" },
  { hash: "#/opportunities", icon: "⭐", label: "Chancen" },
  { hash: "#/watchlist", icon: "👁", label: "Watchlist" },
  { hash: "#/news", icon: "📰", label: "News" },
  { hash: "#/auswertung", icon: "📊", label: "Auswertung" },
  { hash: "#/settings", icon: "⚙", label: "Einstellungen" },
];

const ADVANCED_NAV_ITEMS = [
  { hash: "#/signals", icon: "🌗", label: "Shadow-Setups" },
  { hash: "#/research", icon: "🧠", label: "KI-Analyse (frei)" },
  { hash: "#/calendar", icon: "📅", label: "Kalender" },
  { hash: "#/resolutions", icon: "✅", label: "Entscheidungen" },
  { hash: "#/stats", icon: "📊", label: "Statistik" },
  { hash: "#/analytics", icon: "📐", label: "Statistiken (erweitert)" },
  { hash: "#/quality", icon: "🧹", label: "Datenqualität" },
  { hash: "#/compare", icon: "⚖", label: "Plattform-Vergleich" },
  { hash: "#/providers", icon: "🔌", label: "Datenquellen" },
  { hash: "#/monitoring", icon: "🛰", label: "Scanner-Status" },
  { hash: "#/heatmap", icon: "🗺", label: "Heatmap" },
  { hash: "#/search", icon: "🔎", label: "Suche" },
];

function renderSidebar(activeHash) {
  const el = document.getElementById("sidebar");
  const base = activeHash.split("?")[0].split("/").slice(0, 2).join("/");
  const advancedIsActive = ADVANCED_NAV_ITEMS.some((item) => item.hash === base);
  const advancedOpen = advancedIsActive || sessionStorage.getItem("pmp_advanced_nav_open") === "1";

  const renderItem = (item) =>
    `<a href="${item.hash}" class="${item.hash === base ? "active" : ""}">` +
    `<span>${item.icon}</span><span>${item.label}</span></a>`;

  el.innerHTML =
    `<div class="brand">PolymarketPulse</div>` +
    MAIN_NAV_ITEMS.map(renderItem).join("") +
    `<button type="button" id="advanced-nav-toggle" class="nav-group-toggle">
       <span>🔧</span><span>Erweitert</span><span style="margin-left:auto">${advancedOpen ? "▾" : "▸"}</span>
     </button>` +
    `<div id="advanced-nav-list" style="${advancedOpen ? "" : "display:none"}">` +
    ADVANCED_NAV_ITEMS.map(renderItem).join("") +
    `</div>`;

  document.getElementById("advanced-nav-toggle").onclick = () => {
    const isOpen = sessionStorage.getItem("pmp_advanced_nav_open") === "1";
    sessionStorage.setItem("pmp_advanced_nav_open", isOpen ? "0" : "1");
    renderSidebar(activeHash);
  };
}
