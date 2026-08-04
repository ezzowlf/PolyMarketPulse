async function renderMarketsPage(container, query) {
  const params = new URLSearchParams(query || "");
  const state = {
    provider: params.get("provider") || "",
    search: params.get("search") || "",
    min_liquidity: params.get("min_liquidity") || "",
    limit: 25,
    offset: parseInt(params.get("offset") || "0", 10),
  };

  container.innerHTML = `
    <div class="panel">
      <div class="filters">
        <input id="f-search" placeholder="Suche nach Frage…" value="${state.search}" />
        <select id="f-provider"><option value="">Alle Provider</option></select>
        <input id="f-liquidity" type="number" placeholder="Min. Liquidität" value="${state.min_liquidity}" />
        <button class="btn" id="f-apply">Filtern</button>
      </div>
      <div id="markets-table"><div class="empty-state">Lade Märkte…</div></div>
      <div class="pagination" id="markets-pagination"></div>
    </div>
  `;

  const providers = await Api.providers();
  const providerSelect = document.getElementById("f-provider");
  providers.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name;
    if (p.name === state.provider) opt.selected = true;
    providerSelect.appendChild(opt);
  });

  async function load() {
    const data = await Api.markets({
      provider: state.provider || undefined,
      search: state.search || undefined,
      min_liquidity: state.min_liquidity || undefined,
      limit: state.limit,
      offset: state.offset,
    });
    const tableEl = document.getElementById("markets-table");
    if (data.items.length === 0) {
      tableEl.innerHTML = `<div class="empty-state">Keine Märkte gefunden.</div>`;
    } else {
      tableEl.innerHTML = `
        <table>
          <thead><tr><th>Frage</th><th>Datenquelle</th><th>YES</th><th>Liquidität</th><th>Score</th><th>Ende</th></tr></thead>
          <tbody>
            ${data.items
              .map(
                (m) => `
              <tr class="clickable" data-id="${m.market_id}">
                <td>${m.question}</td>
                <td><span class="badge">${m.provider}</span></td>
                <td>${m.yes_price !== null ? fmtPct(m.yes_price) : "–"}</td>
                <td>$${fmtNum(m.liquidity)}</td>
                <td>${m.opportunity_score !== null ? fmtNum(m.opportunity_score, 1) : "–"}</td>
                <td>${fmtDate(m.end_date)}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      `;
      tableEl.querySelectorAll("tr.clickable").forEach((row) => {
        row.onclick = () => {
          window.location.hash = `#/market/${encodeURIComponent(row.dataset.id)}`;
        };
      });
    }

    const pag = document.getElementById("markets-pagination");
    pag.innerHTML = `
      <button class="btn secondary" id="prev-page" ${state.offset === 0 ? "disabled" : ""}>Zurück</button>
      <span>${state.offset + 1}–${state.offset + data.items.length} von ${data.total}</span>
      <button class="btn secondary" id="next-page" ${state.offset + state.limit >= data.total ? "disabled" : ""}>Weiter</button>
    `;
    document.getElementById("prev-page").onclick = () => {
      state.offset = Math.max(0, state.offset - state.limit);
      load();
    };
    document.getElementById("next-page").onclick = () => {
      state.offset += state.limit;
      load();
    };
  }

  document.getElementById("f-apply").onclick = () => {
    state.search = document.getElementById("f-search").value;
    state.provider = document.getElementById("f-provider").value;
    state.min_liquidity = document.getElementById("f-liquidity").value;
    state.offset = 0;
    load();
  };

  await load();
}
