async function renderSettingsPage(container) {
  const settings = await Api.settings();
  const cost = await Api.costReport(1).catch(() => null);
  const ai = settings.ai;
  container.innerHTML = `
    <div class="panel">
      <h3>KI</h3>
      <div class="widget-grid">
        ${widgetCard({ title: "Status", value: ai.ready ? `<span class="badge green">aktiv</span>` : `<span class="badge red">deaktiviert</span>` })}
        ${widgetCard({ title: "API-Key vorhanden", value: ai.api_key_present ? "ja" : "nein" })}
        ${widgetCard({ title: "Modell", value: ai.model })}
        ${widgetCard({ title: "Kostenlimit pro Analyse", value: "$" + ai.max_cost_per_analysis_usd })}
        ${widgetCard({ title: "Tagesbudget", value: "$" + ai.daily_budget_usd })}
        ${widgetCard({ title: "Heutige Kosten", value: cost ? "$" + fmtNum(cost.spent_today_usd, 6) : "–" })}
        ${widgetCard({ title: "GPT-5-mini-Eskalation", value: ai.escalation_enabled ? "aktiv" : "deaktiviert" })}
      </div>
      ${
        !ai.enabled
          ? `<div class="disclaimer">KI ist deaktiviert. Aktivierung: <code>POLYMARKETPULSE_AI_ENABLED=true</code> und <code>OPENAI_API_KEY=...</code> in der <code>.env</code> setzen, danach den Server neu starten. Aus Sicherheitsgründen kann der API-Key nicht im Browser eingegeben werden.</div>`
          : !ai.api_key_present
          ? `<div class="disclaimer">KI ist aktiviert, aber kein API-Key konfiguriert. <code>OPENAI_API_KEY</code> in der <code>.env</code> setzen und neu starten.</div>`
          : ""
      }
    </div>

    <div class="panel">
      <h3>Daten</h3>
      <table>
        <tbody>
          <tr><td>Standard-Provider</td><td>${settings.default_provider}</td></tr>
          <tr><td>Scan-Limit</td><td>${settings.scan_limit}</td></tr>
          <tr><td>Unveränderte Snapshots speichern</td><td>${settings.store_unchanged_snapshots}</td></tr>
          <tr><td>News-Modul aktiv</td><td>${settings.news_enabled}</td></tr>
          <tr><td>Telegram aktiv</td><td>${settings.telegram_enabled}</td></tr>
        </tbody>
      </table>
      <p class="sub">Aktualisierung erfolgt über den „Märkte aktualisieren“-Button auf der Übersicht — kein automatisches Dauerpolling.</p>
    </div>

    <div class="panel">
      <h3>Analyse-Schwellen</h3>
      <table>
        <tbody>
          <tr><td>Mindestliquidität</td><td>$${fmtNum(settings.thresholds.min_liquidity)}</td></tr>
          <tr><td>Mindestvolumen (24h)</td><td>$${fmtNum(settings.thresholds.min_volume_24h)}</td></tr>
        </tbody>
      </table>
      <p class="sub">
        Diese Werte steuern, welche Märkte beim Scan gespeichert werden (aus <code>.env</code>). Filter für
        Confidence/Edge auf den Seiten „Märkte“ und „Chancen“ lassen sich dort direkt einstellen, ohne Neustart.
      </p>
    </div>

    <div class="panel">
      <h3>Hinweis zu dieser Seite</h3>
      <p class="sub">
        Werte, die den laufenden Serverprozess betreffen (KI-Aktivierung, API-Key, Kostenlimits, Provider),
        werden bewusst nicht im Browser editierbar gemacht — eine Änderung hier hätte ohne Neustart keine
        Wirkung und würde ein funktionsloses Formularfeld vortäuschen. Bitte direkt in <code>.env</code> anpassen.
      </p>
    </div>

    <div class="panel">
      <h3>Benachrichtigungen (Architektur-Vorbereitung)</h3>
      <p class="sub">Telegram bleibt der primäre Kanal (CLI: <code>polymarketpulse telegram-preview</code>). Browser-Benachrichtigungen sind vorbereitet, aber es wird kein echter Push-Dienst angebunden.</p>
      <button class="btn secondary" id="request-notif">Browser-Benachrichtigungen aktivieren (lokale Vorschau)</button>
      <p id="notif-status" class="sub"></p>
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
