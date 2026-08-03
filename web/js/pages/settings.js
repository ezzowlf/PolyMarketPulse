async function renderSettingsPage(container) {
  const settings = await Api.settings();
  container.innerHTML = `
    <div class="panel">
      <h3>Aktuelle Konfiguration (aus .env)</h3>
      <p class="sub">Änderungen erfolgen über die \`.env\`-Datei und einen Neustart — nicht über dieses Formular, um versehentliche Fehlkonfiguration zu vermeiden.</p>
      <table>
        <tbody>
          <tr><td>Umgebung</td><td>${settings.environment}</td></tr>
          <tr><td>Standard-Provider</td><td>${settings.default_provider}</td></tr>
          <tr><td>Scan-Limit</td><td>${settings.scan_limit}</td></tr>
          <tr><td>Unveränderte Snapshots speichern</td><td>${settings.store_unchanged_snapshots}</td></tr>
          <tr><td>News-Modul aktiv</td><td>${settings.news_enabled}</td></tr>
          <tr><td>Telegram aktiv</td><td>${settings.telegram_enabled}</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>Benachrichtigungen (Architektur-Vorbereitung)</h3>
      <p class="sub">Telegram bleibt der primäre Kanal (CLI: <code>polymarketpulse telegram-preview</code>). Browser-Benachrichtigungen sind vorbereitet, aber es wird kein echter Push-Dienst angebunden.</p>
      <button class="btn secondary" id="request-notif">Browser-Benachrichtigungen aktivieren (lokale Vorschau)</button>
      <p id="notif-status" class="sub"></p>
    </div>

    <div class="panel">
      <h3>KI-Assistent (vorbereitet, nicht aktiv)</h3>
      <p class="sub">Architektur für spätere Fragen wie „Warum steigt dieser Markt?“ ist vorgesehen, aber es ist noch keine LLM-Anbindung aktiv. Antworten würden ausschließlich auf gespeicherten Daten basieren.</p>
      <input type="text" placeholder="Frage (noch nicht aktiv)" disabled style="width:100%;background:var(--bg-panel-alt);border:1px solid var(--border);border-radius:8px;padding:8px;color:var(--text-dim)" />
    </div>
  `;

  document.getElementById("request-notif").onclick = async () => {
    const statusEl = document.getElementById("notif-status");
    if (!("Notification" in window)) {
      statusEl.textContent = "Browser unterstützt keine Notifications API.";
      return;
    }
    const permission = await Notification.requestPermission();
    statusEl.textContent = `Berechtigung: ${permission}`;
    if (permission === "granted") {
      new Notification("PolymarketPulse", { body: "Benachrichtigungen sind aktiviert (lokale Vorschau)." });
    }
  };
}
