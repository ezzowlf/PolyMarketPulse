function _isFirstOpenToday() {
  const today = new Date().toISOString().slice(0, 10);
  const last = localStorage.getItem("pmp_last_open_date");
  if (last === today) return false;
  localStorage.setItem("pmp_last_open_date", today);
  return true;
}

function _renderHighlightCard(h) {
  const preisText = h.aktueller_preis !== null && h.aktueller_preis !== undefined ? fmtPct(h.aktueller_preis) : "–";
  const veraenderungText =
    h.veraenderung_seit_erkennung !== null && h.veraenderung_seit_erkennung !== undefined
      ? `${h.veraenderung_seit_erkennung >= 0 ? "+" : ""}${(h.veraenderung_seit_erkennung * 100).toFixed(1)} Punkte seit Erkennung`
      : "noch keine Vergleichsbasis";
  const resolutionText =
    h.tage_bis_resolution !== null && h.tage_bis_resolution !== undefined
      ? h.tage_bis_resolution < 1
        ? "Entscheidung sehr bald"
        : `noch ca. ${Math.round(h.tage_bis_resolution)} Tag(e) bis zur Entscheidung`
      : "Entscheidungszeitpunkt unbekannt";

  return `
    <div class="panel" style="margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <h3 style="margin:0 0 6px"><a href="#/market/${encodeURIComponent(h.market_id)}">${h.frage}</a></h3>
        <span class="badge green">Shadow-Score ${h.shadow_score.toFixed(0)}</span>
      </div>
      <div class="sub">Aktueller Preis: ${preisText} · ${veraenderungText} · ${resolutionText}${h.research_score !== null ? ` · Research-Score ${h.research_score.toFixed(0)}` : ""}</div>
      <div style="margin-top:8px">
        <strong>Warum interessant:</strong>
        <ul>${h.wichtigste_gruende.map((g) => `<li>${g}</li>`).join("") || "<li>–</li>"}</ul>
      </div>
      <div>
        <strong>Zu beachten:</strong>
        <ul>${h.wichtigste_risiken.map((r) => `<li>${r}</li>`).join("") || "<li>–</li>"}</ul>
      </div>
      ${h.letzte_nachricht ? `<div class="sub">Letzte Nachricht: ${h.letzte_nachricht.titel}</div>` : ""}
      <a href="#/research?market=${encodeURIComponent(h.market_id)}">🧠 KI-Analyse zu diesem Markt ansehen →</a>
    </div>
  `;
}

async function renderDashboardPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Startseite…</div>`;
  try {
    const home = await Api.home();
    const t = home.heute;
    const firstToday = _isFirstOpenToday();

    const morgenbericht = firstToday
      ? `
      <div class="panel" style="border-color:var(--accent)">
        <h3 style="margin-top:0">👋 Guten Morgen</h3>
        <p>Heute solltest du besonders diese Märkte beobachten${home.besonders_interessant.length ? "" : " – aktuell gibt es aber nichts Auffälliges."}</p>
      </div>
    `
      : "";

    container.innerHTML = `
      <div class="disclaimer">Research-Hinweis – keine Wettaufforderung. Diese Seite zeigt bewusst nur wenige, sorgfältig ausgewählte Märkte statt einer vollständigen Übersicht.</div>
      ${morgenbericht}
      <div class="widget-grid">
        ${widgetCard({ title: "Märkte mit hoher Aufmerksamkeit", value: t.maerkte_mit_hoher_aufmerksamkeit })}
        ${widgetCard({ title: "Neue Shadow-Setups (24h)", value: t.neue_shadow_setups })}
        ${widgetCard({ title: "Wichtige Nachrichten (24h)", value: t.wichtige_nachrichten })}
        ${widgetCard({ title: "Märkte kurz vor Entscheidung", value: t.maerkte_vor_entscheidung })}
      </div>

      <h2>Heute besonders interessant</h2>
      ${
        home.besonders_interessant.length
          ? home.besonders_interessant.map(_renderHighlightCard).join("")
          : `<div class="empty-state">Aktuell gibt es keine Märkte, die mehrere unabhängige Auffälligkeiten gleichzeitig zeigen. Das ist normal — die meisten Märkte sind an den meisten Tagen unauffällig.</div>`
      }
      <p class="sub">Vollständige Liste aller Shadow-Setups: <a href="#/signals">Shadow-Setups ansehen →</a></p>
    `;
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Fehler beim Laden der Startseite: ${err.message}</div>`;
  }
}
