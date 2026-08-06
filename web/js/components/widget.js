function widgetCard({ title, value, sub, listItems }) {
  const list = listItems
    ? `<ul>${listItems.map((i) => `<li>${i}</li>`).join("") || "<li>Keine Daten</li>"}</ul>`
    : "";
  return (
    `<div class="widget"><h3>${title}</h3>` +
    (value !== undefined ? `<div class="value">${value}</div>` : "") +
    (sub ? `<div class="sub">${sub}</div>` : "") +
    list +
    `</div>`
  );
}

function fmtNum(n, digits = 0) {
  if (n === null || n === undefined) return "–";
  return Number(n).toLocaleString("de-DE", { maximumFractionDigits: digits });
}

function fmtPct(n) {
  if (n === null || n === undefined) return "–";
  return (Number(n) * 100).toFixed(1) + "%";
}

function renderBreakdownTable(breakdown) {
  const entries = Object.entries(breakdown || {});
  if (entries.length === 0) return `<div class="empty-state">Keine ausgewerteten Signale.</div>`;
  return `
    <table>
      <thead><tr><th>Gruppe</th><th>Anzahl</th><th>Trefferquote</th><th>Ø Rendite</th></tr></thead>
      <tbody>
        ${entries
          .map(
            ([key, v]) =>
              `<tr><td>${key}</td><td>${v.count}</td><td>${v.hit_rate !== null ? fmtPct(v.hit_rate) : "–"}</td><td>${v.average_simulated_return !== null ? fmtNum(v.average_simulated_return, 3) : "–"}</td></tr>`
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function fmtDate(iso) {
  if (!iso) return "–";
  try {
    return new Date(iso).toLocaleString("de-DE");
  } catch {
    return iso;
  }
}

const RESOLUTION_STATUS_DE = {
  unresolved: "offen",
  proposed: "Auflösung vorgeschlagen",
  resolved: "entschieden",
  cancelled: "storniert",
  invalid: "ungültig",
  disputed: "umstritten",
  unknown: "unbekannt",
};

function fmtStatus(status) {
  return RESOLUTION_STATUS_DE[status] || status || "–";
}

const OPPORTUNITY_STATUS_BADGE = {
  "Interessant": "green",
  "Beobachten": "yellow",
  "Kurz vor Deadline": "yellow",
  "Keine klare Edge": "",
  "Datenlage unzureichend": "",
  "Preis fehlt": "red",
};

function statusBadge(status) {
  const cls = OPPORTUNITY_STATUS_BADGE[status] ?? "";
  return `<span class="badge ${cls}">${status}</span>`;
}

function fmtDeadline(hours) {
  if (hours === null || hours === undefined) return "unbekannt";
  if (hours < 0) return "abgelaufen";
  if (hours < 24) return `${Math.round(hours)} Std.`;
  const days = hours / 24;
  return `${days < 10 ? days.toFixed(1) : Math.round(days)} Tage`;
}

function fmtEdgePp(edge) {
  if (edge === null || edge === undefined) return "–";
  return (edge * 100).toFixed(1) + " pp";
}

function fmtChangeArrow(from, to, formatter) {
  if (from === null || from === undefined || to === null || to === undefined) return "";
  const f = formatter || ((v) => v);
  if (Math.abs(to - from) < 1e-9) return "";
  return ` <span class="sub">(${f(from)} → ${f(to)})</span>`;
}
