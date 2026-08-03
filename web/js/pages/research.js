async function renderResearchPage(container, query) {
  const params = new URLSearchParams(query || "");
  const presetMarketId = params.get("market") || "";

  container.innerHTML = `
    <div class="disclaimer">Kein LLM, keine Halluzination: jede Aussage hier basiert ausschließlich auf gespeicherten Snapshots, Signalen und News. Keine Handlungsempfehlung.</div>
    <div class="panel">
      <div class="filters">
        <input id="research-market-id" placeholder="Markt-ID (z.B. aus der Marktdetailseite)" value="${presetMarketId}" style="min-width:280px" />
        <select id="research-mode">
          <option value="movement">Warum bewegt sich der Markt?</option>
          <option value="news">Welche News waren relevant?</option>
          <option value="signals">Welche Signale lagen vorher vor?</option>
          <option value="similar">Welche historischen Märkte waren vergleichbar?</option>
        </select>
        <button class="btn" id="research-ask">Fragen</button>
      </div>
      <div id="research-result"></div>
    </div>
  `;

  async function ask() {
    const marketId = document.getElementById("research-market-id").value.trim();
    const mode = document.getElementById("research-mode").value;
    const resultEl = document.getElementById("research-result");
    if (!marketId) {
      resultEl.innerHTML = `<div class="empty-state">Bitte eine Markt-ID eingeben.</div>`;
      return;
    }
    resultEl.innerHTML = `<div class="empty-state">Analysiere…</div>`;
    try {
      const explanation = await Api.explain(marketId, mode);
      resultEl.innerHTML = `
        <h3>${explanation.question}</h3>
        <ul>${explanation.statements.map((s) => `<li>${s}</li>`).join("")}</ul>
      `;
    } catch (err) {
      resultEl.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
    }
  }

  document.getElementById("research-ask").onclick = ask;
  if (presetMarketId) ask();
}
