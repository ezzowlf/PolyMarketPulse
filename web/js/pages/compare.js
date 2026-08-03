async function renderComparePage(container) {
  container.innerHTML = `<div class="empty-state">Lade Provider-Vergleich…</div>`;
  const results = await Api.compare();
  if (results.length === 0) {
    container.innerHTML = `<div class="empty-state">Keine Kandidaten für Cross-Provider-Vergleich gefunden (mehrere Provider mit ähnlichen Markt-Fragen nötig).</div>`;
    return;
  }
  container.innerHTML = `
    <div class="disclaimer">Nur Beobachtung von Preisunterschieden zwischen textlich ähnlichen Märkten verschiedener Provider — keine Bestätigung, dass es sich um denselben Markt handelt, und keine Handelsempfehlung.</div>
    <div class="panel">
      <table>
        <thead><tr><th>Markt A</th><th>YES A</th><th>Markt B</th><th>YES B</th><th>Abweichung</th><th>Textähnlichkeit</th><th>Status</th></tr></thead>
        <tbody>
          ${results
            .map(
              (r) => `
            <tr>
              <td>${r.question_a} <span class="badge">${r.provider_a}</span></td>
              <td>${r.yes_price_a !== null ? fmtPct(r.yes_price_a) : "–"}</td>
              <td>${r.question_b} <span class="badge">${r.provider_b}</span></td>
              <td>${r.yes_price_b !== null ? fmtPct(r.yes_price_b) : "–"}</td>
              <td>${r.divergence !== null ? fmtPct(r.divergence) : "–"}</td>
              <td>${(r.text_similarity * 100).toFixed(0)}%</td>
              <td><span class="badge yellow">${r.status}</span></td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    </div>
  `;
}
