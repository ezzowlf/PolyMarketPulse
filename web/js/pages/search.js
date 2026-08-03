async function renderSearchPage(container, query) {
  const params = new URLSearchParams(query || "");
  const q = params.get("q") || "";

  container.innerHTML = `
    <div class="panel">
      <div class="filters">
        <input id="search-input" placeholder="Markt, News, Provider, Kategorie, Signal, Resolution…" value="${q}" style="min-width:320px" />
        <button class="btn" id="search-go">Suchen</button>
      </div>
      <div id="search-results"></div>
    </div>
  `;

  async function run() {
    const term = document.getElementById("search-input").value.trim();
    const resultsEl = document.getElementById("search-results");
    if (term.length < 2) {
      resultsEl.innerHTML = `<div class="empty-state">Mindestens 2 Zeichen eingeben.</div>`;
      return;
    }
    resultsEl.innerHTML = `<div class="empty-state">Suche…</div>`;
    try {
      const results = await Api.search(term);
      resultsEl.innerHTML = `
        <h4>Märkte (${results.markets.length})</h4>
        <ul>${results.markets.map((m) => `<li><a href="#/market/${encodeURIComponent(m.market_id)}">${m.question}</a> <span class="badge">${m.provider}</span></li>`).join("") || "<li>keine</li>"}</ul>
        <h4>News (${results.news.length})</h4>
        <ul>${results.news.map((n) => `<li>${n.title} — ${n.source}</li>`).join("") || "<li>keine</li>"}</ul>
        <h4>Signale (${results.signals.length})</h4>
        <ul>${results.signals.map((s) => `<li>${s.signal_type} — ${s.provider}</li>`).join("") || "<li>keine</li>"}</ul>
        <h4>Resolutionen (${results.resolutions.length})</h4>
        <ul>${results.resolutions.map((r) => `<li>${r.provider} — ${r.status} — ${r.winning_outcome || "–"}</li>`).join("") || "<li>keine</li>"}</ul>
      `;
    } catch (err) {
      resultsEl.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
    }
  }

  document.getElementById("search-go").onclick = run;
  document.getElementById("search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") run();
  });
  if (q) run();
}
