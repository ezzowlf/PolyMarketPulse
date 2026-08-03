async function renderHeatmapPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Heatmap…</div>`;
  const items = await Api.heatmap();
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state">Keine Daten für Heatmap vorhanden.</div>`;
    return;
  }

  let metric = "opportunity_score";
  const metrics = {
    opportunity_score: "Research Score",
    liquidity: "Liquidität",
    one_day_change: "Preisänderung",
    volume_24h: "Volumen",
  };

  container.innerHTML = `
    <div class="filters">
      <select id="metric-select">
        ${Object.entries(metrics).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
      </select>
    </div>
    <div class="panel" id="heatmap-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:6px;"></div>
  `;

  function colorFor(value, min, max) {
    if (value === null || value === undefined || max === min) return "#22304a";
    const t = (value - min) / (max - min);
    const r = Math.round(255 * t);
    const g = Math.round(180 * (1 - Math.abs(t - 0.5) * 2) + 40);
    const b = Math.round(255 * (1 - t));
    return `rgb(${r},${g},${b})`;
  }

  function draw() {
    const values = items.map((i) => i[metric]).filter((v) => v !== null && v !== undefined);
    const min = Math.min(...values), max = Math.max(...values);
    const grid = document.getElementById("heatmap-grid");
    grid.innerHTML = items
      .map(
        (i) => `
      <div class="clickable" data-id="${i.market_id}" title="${i.question}"
           style="background:${colorFor(i[metric], min, max)};border-radius:8px;padding:10px;font-size:11px;color:#0b0f16;font-weight:600;cursor:pointer;min-height:60px;">
        ${i.question.slice(0, 40)}${i.question.length > 40 ? "…" : ""}
        <div style="margin-top:6px;font-weight:700">${i[metric] !== null ? Number(i[metric]).toFixed(2) : "–"}</div>
      </div>`
      )
      .join("");
    grid.querySelectorAll("[data-id]").forEach((el) => {
      el.onclick = () => (window.location.hash = `#/market/${encodeURIComponent(el.dataset.id)}`);
    });
  }

  document.getElementById("metric-select").onchange = (e) => {
    metric = e.target.value;
    draw();
  };
  draw();
}
