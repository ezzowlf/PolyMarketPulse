async function renderCalendarPage(container) {
  container.innerHTML = `<div class="empty-state">Lade Kalender…</div>`;
  const items = await Api.calendar();
  if (items.length === 0) {
    container.innerHTML = `<div class="empty-state">Keine bevorstehenden Auflösungen gefunden.</div>`;
    return;
  }
  const byDate = {};
  items.forEach((i) => {
    const day = (i.end_date || "").slice(0, 10);
    byDate[day] = byDate[day] || [];
    byDate[day].push(i);
  });
  container.innerHTML = `
    <div class="panel">
      ${Object.entries(byDate)
        .sort(([a], [b]) => (a > b ? 1 : -1))
        .map(
          ([day, entries]) => `
        <h4>${day}</h4>
        <ul>${entries.map((e) => `<li>${e.question} <span class="badge">${e.provider}</span></li>`).join("")}</ul>`
        )
        .join("")}
    </div>
  `;
}
