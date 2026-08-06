let _oppState = { sort: "opportunity_score", minEdge: "", minConfidence: "", requirePrice: true, category: "" };

function _oppRow(o) {
  return `
    <tr onclick="location.hash='#/market/${encodeURIComponent(o.market_id)}'" style="cursor:pointer">
      <td>${o.question}</td>
      <td>${o.category || "–"}</td>
      <td>${fmtPct(o.market_yes_probability)}</td>
      <td>${fmtPct(o.estimated_yes_probability)}</td>
      <td>${fmtEdgePp(o.net_yes_edge)}</td>
      <td>${fmtNum(o.confidence_score, 0)}</td>
      <td>${fmtNum(o.data_quality_score, 0)}</td>
      <td>${fmtDeadline(o.deadline_hours)}</td>
      <td title="Opportunity Score: kombiniert Edge, Confidence, Datenqualität, Liquidität, Spread und Deadline — nicht nur die reine Edge.">${o.opportunity_score.toFixed(1)}</td>
      <td>${statusBadge(o.status)}</td>
    </tr>
  `;
}

async function _loadOpportunities(container) {
  const list = document.getElementById("opp-results");
  list.innerHTML = `<div class="empty-state">Lade Chancen…</div>`;
  try {
    const params = { sort: _oppState.sort, require_price: _oppState.requirePrice };
    if (_oppState.minEdge) params.min_edge = Number(_oppState.minEdge) / 100;
    if (_oppState.minConfidence) params.min_confidence = Number(_oppState.minConfidence);
    if (_oppState.category) params.category = _oppState.category;
    const items = await Api.opportunities(params);
    list.innerHTML = items.length
      ? `<table><thead><tr><th>Frage</th><th>Kategorie</th><th>Markt</th><th>Engine</th><th>Edge</th><th>Confidence</th><th>Datenqualität</th><th>Deadline</th><th title="Kombinierter Score aus Edge, Confidence, Datenqualität, Liquidität, Spread, Deadline">Opportunity Score</th><th>Status</th></tr></thead>
        <tbody>${items.map(_oppRow).join("")}</tbody></table>`
      : `<div class="empty-state">Keine Märkte erfüllen die aktuellen Filter.</div>`;
  } catch (err) {
    list.innerHTML = `<div class="empty-state">Fehler: ${err.message}</div>`;
  }
}

async function renderOpportunitiesPage(container) {
  container.innerHTML = `
    <div class="disclaimer">
      Der Opportunity Score kombiniert Edge, Confidence, Datenqualität, Liquidität, Spread und Deadline —
      ein Markt mit großer Edge aber geringer Confidence steht hier nicht automatisch über einem Markt mit
      kleinerer, aber gut abgesicherter Edge. Keine Kaufaufforderung, keine Gewinngarantie.
    </div>
    <div class="panel">
      <h3>Chancen</h3>
      <div class="widget-grid" style="align-items:end">
        <label>Sortierung
          <select id="opp-sort">
            <option value="opportunity_score">Opportunity Score</option>
            <option value="edge">Größte Edge</option>
            <option value="confidence">Höchste Confidence</option>
            <option value="deadline">Nächste Deadline</option>
            <option value="liquidity">Höchste Liquidität</option>
            <option value="volume">Höchstes Volumen</option>
            <option value="last_seen">Zuletzt aktualisiert</option>
          </select>
        </label>
        <label>Mindest-Edge (pp)
          <input type="number" id="opp-min-edge" placeholder="z.B. 5" />
        </label>
        <label>Mindest-Confidence
          <input type="number" id="opp-min-confidence" placeholder="z.B. 50" />
        </label>
        <label>Kategorie
          <input type="text" id="opp-category" placeholder="Kategorie" />
        </label>
        <label><input type="checkbox" id="opp-require-price" checked /> Nur Märkte mit Preis</label>
        <button class="btn" id="opp-apply">Anwenden</button>
      </div>
    </div>
    <div class="panel" id="opp-results"></div>
  `;

  document.getElementById("opp-sort").value = _oppState.sort;
  document.getElementById("opp-min-edge").value = _oppState.minEdge;
  document.getElementById("opp-min-confidence").value = _oppState.minConfidence;
  document.getElementById("opp-category").value = _oppState.category;
  document.getElementById("opp-require-price").checked = _oppState.requirePrice;

  document.getElementById("opp-apply").onclick = () => {
    _oppState = {
      sort: document.getElementById("opp-sort").value,
      minEdge: document.getElementById("opp-min-edge").value,
      minConfidence: document.getElementById("opp-min-confidence").value,
      category: document.getElementById("opp-category").value,
      requirePrice: document.getElementById("opp-require-price").checked,
    };
    _loadOpportunities(container);
  };

  await _loadOpportunities(container);
}
