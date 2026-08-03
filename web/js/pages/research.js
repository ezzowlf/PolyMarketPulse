async function renderResearchPage(container, query) {
  const params = new URLSearchParams(query || "");
  const presetMarketId = params.get("market") || "";

  container.innerHTML = `
    <div class="disclaimer">Retrieval-Modul: keine Halluzination, jede Aussage basiert ausschließlich auf gespeicherten Snapshots, Signalen und News. Keine Handlungsempfehlung.</div>
    <div class="panel">
      <h3>Datenbasierte Erklärung (ohne KI)</h3>
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

    <div class="panel">
      <h3>🧠 KI-Research – keine Wettaufforderung</h3>
      <div id="ai-status-banner" class="sub">Prüfe AI-Status…</div>
      <div class="filters">
        <input id="ai-market-id" placeholder="Markt-ID (optional)" value="${presetMarketId}" style="min-width:220px" />
        <input id="ai-question" placeholder="Eigene Frage (optional, sonst: Markt erklären)" style="min-width:320px" />
        <button class="btn" id="ai-analyze">Analysieren</button>
      </div>
      <div id="ai-result"></div>
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

  // --- AI panel ---
  const banner = document.getElementById("ai-status-banner");
  let aiReady = false;
  try {
    const status = await Api.aiStatus();
    aiReady = status.ready;
    banner.textContent = status.ready
      ? `AI aktiv — Modell: ${status.model} (Cache-TTL ${status.cache_ttl_seconds}s)`
      : `AI nicht verfügbar: ${status.reason || "deaktiviert"}`;
    banner.className = status.ready ? "sub" : "sub";
  } catch (err) {
    banner.textContent = `AI-Status konnte nicht geladen werden: ${err.message}`;
  }

  document.getElementById("ai-analyze").onclick = async () => {
    const marketId = document.getElementById("ai-market-id").value.trim() || null;
    const question = document.getElementById("ai-question").value.trim();
    const resultEl = document.getElementById("ai-result");

    if (!aiReady) {
      resultEl.innerHTML = `<div class="empty-state">AI ist deaktiviert (POLYMARKETPULSE_AI_ENABLED / OPENAI_API_KEY nicht gesetzt).</div>`;
      return;
    }
    if (!marketId && !question) {
      resultEl.innerHTML = `<div class="empty-state">Bitte Markt-ID und/oder eine Frage angeben.</div>`;
      return;
    }

    resultEl.innerHTML = `<div class="empty-state">KI analysiert…</div>`;
    try {
      const response = question
        ? await Api.aiAsk(question, marketId)
        : await Api.aiExplainMarket(marketId);
      renderAiResult(resultEl, response);
    } catch (err) {
      resultEl.innerHTML = `<div class="empty-state">KI-Fehler (${err.status || "?"}): ${err.detail || err.message}</div>`;
    }
  };
}

function renderAiResult(container, response) {
  const r = response.result;
  const meta = response.meta;
  const factorList = (factors) =>
    factors.length
      ? `<ul>${factors.map((f) => `<li><span class="badge ${f.strength === "high" ? "green" : f.strength === "low" ? "yellow" : ""}">${f.strength}</span> ${f.factor} — <span class="sub">${f.evidence}</span></li>`).join("")}</ul>`
      : `<div class="sub">keine</div>`;

  container.innerHTML = `
    <div class="disclaimer">${r.disclaimer}</div>
    <p><strong>${r.summary}</strong></p>
    <p>${r.market_move_explanation}</p>
    <h4>Pro-Faktoren</h4>
    ${factorList(r.supporting_factors)}
    <h4>Contra-Faktoren</h4>
    ${factorList(r.opposing_factors)}
    <h4>Relevante News</h4>
    ${r.relevant_news.length ? `<ul>${r.relevant_news.map((n) => `<li>${n}</li>`).join("")}</ul>` : `<div class="sub">keine</div>`}
    <h4>Datenlücken</h4>
    ${r.data_gaps.length ? `<ul>${r.data_gaps.map((g) => `<li>${g}</li>`).join("")}</ul>` : `<div class="sub">keine</div>`}
    <h4>Unsicherheiten</h4>
    ${r.uncertainties.length ? `<ul>${r.uncertainties.map((u) => `<li>${u}</li>`).join("")}</ul>` : `<div class="sub">keine</div>`}
    <h4>Quellen</h4>
    <div class="sub">${r.source_ids.join(", ") || "keine"}</div>
    <p class="sub">Confidence (Kontextabdeckung, keine Gewinnwahrscheinlichkeit): ${(r.confidence_in_analysis * 100).toFixed(0)}%</p>
    <p class="sub">Modell: ${meta.model} · ${meta.cached ? "aus Cache" : "neu berechnet"} · ${fmtDate(meta.created_at)} · Analyse-ID ${meta.analysis_id}</p>
  `;
}
