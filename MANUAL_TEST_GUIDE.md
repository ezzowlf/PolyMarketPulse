# PolyMarketPulse manuell testen

1. Im Projektordner doppelt auf `START_POLYMARKETPULSE.bat` klicken. Beim ersten Start zuvor einmal die Installation aus der README durchführen und `.env.example` nach `.env` kopieren.
2. Nach der Meldung „bereit“ im Browser `http://127.0.0.1:8000` öffnen. Zum Beenden `STOP_POLYMARKETPULSE.bat` ausführen.
3. Auf **Übersicht** prüfen, ob Märkte und Zeitstempel sichtbar sind. Eine rote Fehlerseite oder „API nicht erreichbar“ ist ein Fehler.
4. Unter **Märkte** den Detailmarkt öffnen. Oben müssen Marktpreis, veröffentlichte Prognose, Entscheidungsstatus, Vertrauen, Datenlage und Deadline verständlich erscheinen.
5. Failure Case öffnen: `http://127.0.0.1:8000/#/market/polymarket%3A2910437`. Erwartet: „Datenquelle derzeit nicht erreichbar“, keine veröffentlichte Prognose und ein erklärter Backoff. Der Ausfall darf nicht wie „keine Evidenz“ wirken.
6. Clarity Act öffnen: `http://127.0.0.1:8000/#/market/polymarket%3A1163699`. Erwartet: House-Passage als belegter Schritt; Senate Vote bleibt der nächste offene Schritt.
7. Hormuz öffnen: `http://127.0.0.1:8000/#/market/2774056`. Erwartet: PortWatch-Transitquelle und ein degradierter Zustand. Niedrige Transitwerte sind keine Behauptung einer vollständigen Schließung.
8. Cross-Market-Fall öffnen: `http://127.0.0.1:8000/#/market/polymarket%3A2910438`. Im Advanced-Bereich darf eine Beziehung nur mit nachvollziehbarer Herkunft auftauchen; sie darf keinen Preis kopieren oder eine Prognose erzwingen.
9. Prüfen Sie bei jedem Detailmarkt **Warum**, **Future Map**, **Szenarien**, **Quellen** und **Was würde sich ändern?**. Eine fehlende veröffentlichte Prognose ist korrekt, wenn die Seite die fehlende unabhängige Evidenz erklärt.
10. Unter **Erweitert** können technische Details wie Research-Verlauf und Audits eingesehen werden. Melden Sie einen Fehler, wenn Marktpreis, Status oder Quellen zwischen Übersicht und Marktdetail widersprechen oder wenn eine leere Fehlerseite erscheint.

Hinweis: PolyMarketPulse ist eine lesende Research-Anwendung. Sie platziert keine Orders und stellt keine Wett- oder Gewinnaufforderung dar.
