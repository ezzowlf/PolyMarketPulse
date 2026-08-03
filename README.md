# PolymarketPulse

Eine **rein lesende** Research-Plattform für Prediction Markets (aktuell primär Polymarket, Architektur für weitere Provider vorbereitet). Sie lädt Märkte, bewertet deren Forschungsrelevanz, erkennt Auflösungen, speichert Zeitreihen in SQLite und stellt die Daten sowohl über eine CLI als auch über ein REST-API + Web-Dashboard bereit.

> Wichtig: Der `Research-Score` ist **kein erwarteter Gewinn und kein Kauf-Signal**. Er priorisiert liquide, aktive und bewegte Märkte mit nachvollziehbaren Kriterien. Eine echte Edge entsteht erst durch eine unabhängig geschätzte faire Wahrscheinlichkeit und anschließende Kalibrierung anhand historischer Ergebnisse.

## Architektur

```text
Browser  →  Dashboard (statisches HTML/JS)  →  REST API (FastAPI)  →  Research Engine  →  Provider  →  SQLite
                                                        ↑
                                                CLI (weiterhin vollständig eigenständig nutzbar)
```

- **Provider-Schicht** (`src/polymarketpulse/providers/`): gemeinsame Schnittstelle `PredictionMarketProvider`, Registry, ein Adapter je Plattform.
- **Research Engine**: `scoring.py` (transparenter Score), `signals.py` (typisierte, erklärbare Signalereignisse), `stats.py` (Auswertung), `matching.py` (Cross-Provider-Kandidaten + Preisdivergenz), `news/` (RSS-Abruf, Klassifizierung, Verknüpfung, Reaktionsmessung), `data_quality.py` (Datenqualitätsprüfung), `price_analytics.py` (gleitende Durchschnitte, Volatilität, Trends), `performance.py` (virtuelle Equity-Kurve/Drawdown), `explain.py` (rein datenbasiertes Analysemodul, kein LLM).
- **Storage**: `storage.py` + `migrations.py` (nummerierte, idempotente Migrationen, kein Datenverlust bei bestehenden DBs).
- **REST API**: `api.py` (FastAPI, ausschließlich lesend + Watchlist-CRUD, Swagger unter `/docs`).
- **Dashboard**: `web/` (Vanilla HTML/CSS/JS, kein Build-Schritt, wird von der API unter `/` mit ausgeliefert).
- **CLI**: `cli.py` — bleibt vollständig erhalten und unabhängig von API/Dashboard nutzbar.

## Provider

| Provider | Marktlisten | Preise | Orderbuch | Volumen | Liquidität | Resolution | Auth nötig | Echtgeld |
|---|---|---|---|---|---|---|---|---|
| polymarket | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ | ja |
| manifold | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ | nein (Spielgeld) |
| predictit | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ja |
| kalshi | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (nicht implementiert) | ja |
| metaculus | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (nicht implementiert) | nein |

Aktuell live abrufbar: **Polymarket, Manifold, PredictIt** (alle drei öffentlich, ohne Zugangsdaten). Kalshi und Metaculus sind als Platzhalter mit ehrlicher Capability-Angabe hinterlegt (`NotImplementedError`), bis eine zulässige Authentifizierung geklärt ist.

## Installation unter Windows

```powershell
cd polymarketpulse
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## CLI

```powershell
python -m polymarketpulse scan
python -m polymarketpulse scan --provider polymarket --limit 20
python -m polymarketpulse scan --provider all --limit 20
python -m polymarketpulse markets --provider manifold
python -m polymarketpulse providers
python -m polymarketpulse provider-info polymarket
python -m polymarketpulse resolutions --provider polymarket
python -m polymarketpulse signals
python -m polymarketpulse signal-stats
python -m polymarketpulse news-fetch
python -m polymarketpulse db-migrate
python -m polymarketpulse db-status
python -m polymarketpulse telegram-preview
python -m polymarketpulse market-history <market_id>
python -m polymarketpulse export-signals --format csv
python -m polymarketpulse quality --provider polymarket
python -m polymarketpulse performance
python -m polymarketpulse search "Fed rate"
python -m polymarketpulse explain <market_id> --mode movement
```

(Falls das Skript `polymarketpulse` nach der Installation nicht im PATH gefunden wird, funktioniert alternativ `python -m polymarketpulse.cli <command>`.)

## Dashboard + REST API starten

```powershell
python -m polymarketpulse serve
```

Öffnet die API auf `http://127.0.0.1:8000` (Swagger unter `/docs`) und liefert das Dashboard unter `/` aus. Das Dashboard liest **ausschließlich aus der lokalen SQLite-Datenbank** — es fragt bei jedem Seitenaufruf keine Provider live ab. Neue Daten kommen ausschließlich über `polymarketpulse scan` (CLI oder als geplanter Task).

Seiten: Dashboard, Märkte, Marktdetail (Charts, historische Analyse, Signalhistorie, News), Watchlist, Chancen (Signale), Research (datenbasierte Erklärungen), News, Kalender, Resolutionen, Simulation, Performance, Statistik, Analytics, Data Quality, Provider-Vergleich (Cross-Provider-Preisdivergenz), Provider (Capability-Dashboard), Scanner-Monitoring, Heatmap, Suche, Einstellungen.

### REST-Endpunkte

`GET /health`, `/providers`, `/provider/{name}`, `/providers/status`, `/markets`, `/market/{id}`, `/signals`, `/signal/{id}`, `/stats`, `/news`, `/history/{market_id}`, `/history/full/{market_id}`, `/watchlist`, `/calendar`, `/heatmap`, `/analytics`, `/settings`, `/quality`, `/performance`, `/simulation`, `/resolutions`, `/search`, `/compare`, `/explain/{market_id}` · `POST /watchlist` · `DELETE /watchlist/{id}`. Alle Antworten sind JSON, keine HTML-Ausgabe über die API. Keine Order-, Wallet- oder Tradingendpunkte (per Test abgesichert).

## Tests und Lint

```powershell
pytest -q
ruff check .
```

## Telegram (optional, standardmäßig aus)

Telegram-Versand ist nur aktiv, wenn **beide** Bedingungen erfüllt sind:

1. `POLYMARKETPULSE_TELEGRAM_ENABLED=true` in `.env`
2. der Befehl wird mit `--send-alerts` aufgerufen: `python -m polymarketpulse scan --send-alerts`

Bereits einmal alarmierte Märkte werden 24 Stunden lang nicht erneut gemeldet. Vorschau-Typen (nie echter Versand ohne die obigen Bedingungen): neues Signal, Markt-Resolution, Tagesstatistik, Provider-Ausfall — alle mit der Kennzeichnung „Research-Hinweis – keine Wettaufforderung“.

Browser-Benachrichtigungen sind auf der Einstellungen-Seite als Architektur vorbereitet (lokale `Notification`-API-Berechtigung), es ist aber kein echter Push-Dienst angebunden.

## Datenbank

- Standardpfad: `data/polymarketpulse.db` (per `.env` änderbar), WAL-Modus für parallele Lese-/Schreibzugriffe (CLI + API gleichzeitig).
- Migrationen sind nummeriert und idempotent (`schema_migrations`-Tabelle, `db-migrate`-Befehl); bestehende Daten werden nie gelöscht (verifiziert an einer Kopie der produktiven DB).
- Wichtige Tabellen: `providers`, `markets`, `market_snapshots`, `price_history`, `market_resolutions`, `research_signals`, `signal_evaluations`, `news_events`, `news_market_links`, `news_market_reactions`, `market_matches`, `watchlist_items`, `data_quality_reports`, `analysis_runs`, `model_metrics`.
- Eindeutige Schlüssel berücksichtigen Provider + providereigene Markt-ID (`UNIQUE(provider, provider_market_id)`).
- Alle Zeitstempel sind UTC (ISO-8601).

## Research-Signale

Typisierte, erklärbare Ereignisse (kein „sicherer Gewinn“, keine Kaufaufforderung): `LIQUIDITY_SURGE`, `VOLUME_SURGE`, `SPREAD_COMPRESSION`, `SPREAD_EXPANSION`, `PRICE_MOMENTUM`, `PRICE_REVERSAL`, `NEW_MARKET`, `RESOLUTION_APPROACHING`, `DATA_QUALITY_WARNING`, `CROSS_PROVIDER_DIVERGENCE` (letzteres über `matching.py` vorbereitet). Jedes Signal wird bei Marktauflösung automatisch simuliert ausgewertet (1 virtuelle Einheit, nie echtes Geld) — Trefferquote, simulierte Rendite, Haltedauer, max. Vor-/Nachteil.

## Data Quality Engine

`data_quality.py` prüft bei jedem Scan pro Markt: fehlende Pflichtfelder, ungültige Preise (außerhalb [0,1]), YES+NO-Konsistenz, negative Volumen/Liquidität, unplausible Spreads, Start-/Enddatum-Reihenfolge und fehlende Rohdatenfelder. Jeder Markt erhält einen Score 0–100 mit itemisierten Gründen (`issues` und `checks_passed`) — nie eine Blackbox-Zahl. `assess_snapshot_sequence()` prüft zusätzlich Snapshot-Historien auf doppelte oder unsortierte Zeitstempel. Ergebnisse landen in `data_quality_reports` und sind über `GET /quality` / `polymarketpulse quality` abrufbar.

## Historische Preisanalyse

`price_analytics.py` berechnet aus den gespeicherten Snapshots je Markt: Preisänderung (absolut/prozentual), gleitende Durchschnitte (kurz/lang), Volatilität (Standardabweichung der Preisänderungen), Ø-Volumen, maximale Einzelbewegung, Anzahl Trendwechsel, Ø-Zeit zwischen Updates sowie Liquiditäts-/Spread-Trend. Jede Kennzahl ist eine dokumentierte, nachvollziehbare Formel — kein Modell. Abrufbar über `GET /history/full/{market_id}`.

## Resolution Engine

Erkennt automatisch vier Endzustände: `resolved`, `cancelled`, `invalid`, `disputed` (aus `closed`-Flag, Outcome-Preisen und UMA-Proposer-Status bei Polymarket). Gespeichert werden Auflösungszeit, Quelle, Gewinner, Finalpreise, Status und Erkennungszeitpunkt (`market_resolutions`); wiederholte Aufrufe sind idempotent. Noch offene Signale werden bei Auflösung automatisch simuliert ausgewertet — bei `cancelled`/`invalid`/`disputed` ohne Gewinner wird die virtuelle Position als neutral (0 P&L) gebucht, nie als Verlust fehlinterpretiert.

## Virtuelle Performance Engine & Simulation

Jedes Signal wird bei Auflösung mit 1.0 virtueller Einheit simuliert (`signal_evaluations`): Einstiegspreis, Ergebnis, korrekt/falsch, simulierte Rendite, Haltedauer, maximaler zwischenzeitlicher Vor-/Nachteil. `performance.py` aggregiert daraus eine chronologische Equity-Kurve (laufende Summe, kein Compounding, keine parallele Kapitalallokation) mit Max-Drawdown, Ø-Rendite und Trefferquote — abrufbar über `GET /performance` / `GET /simulation` und die Dashboard-Seiten „Performance“/„Simulation“. Zu keinem Zeitpunkt echtes Geld.

## Cross-Provider-Intelligence

`matching.py` findet Kandidaten ähnlicher Fragen über Provider hinweg (Textähnlichkeit, Datumsnähe, Outcome-Struktur, Kategorie) und berechnet die reine Preisdifferenz (`compute_divergence`) — ohne jemals zu behaupten, zwei Märkte seien identisch (`status` bleibt `candidate`, bis ein Mensch bestätigt). Sichtbar unter `GET /compare` / Dashboard-Seite „Provider-Vergleich“.

## News Intelligence

`news/reactions.py` vergleicht den Preis vor und nach einer verknüpften News-Meldung (konfigurierbares Zeitfenster, Standard 24h) und markiert, ob eine Reaktion über der Schwelle (2 Prozentpunkte) stattfand — rein beobachtend, keine Kausalitätsbehauptung.

## Erklärbares Analysemodul (kein LLM)

`explain.py` beantwortet vier Fragetypen ausschließlich durch Retrieval und Arithmetik über SQLite — kein generatives Modell, keine Halluzination: „Warum bewegt sich der Markt?“, „Welche News waren relevant?“, „Welche Signale lagen vorher vor?“, „Welche historischen Märkte waren vergleichbar?“. Jede Aussage kommt mit den zugrundeliegenden Datenbank-Zeilen als `evidence`. Abrufbar über `GET /explain/{market_id}`, CLI `explain` und die Dashboard-Seite „Research“.

## Bekannte Einschränkungen

- Kalshi und Metaculus sind noch nicht live angebunden (siehe Tabelle oben).
- PredictIt liefert keine Volumen-/Liquiditätsdaten und keinen separaten Resolution-Feed.
- `CROSS_PROVIDER_DIVERGENCE` und Cross-Provider-Matching liefern nur `status='candidate'` — nichts wird automatisch als identisch bestätigt; es gibt noch keine UI zum manuellen Bestätigen/Ablehnen.
- News-Matching und Reaktionsmessung sind einfache, auditierbare Heuristiken (Begriffsabgleich, Preisfenster-Vergleich), keine echte NLP-Entitätserkennung oder Kausalanalyse.
- Charts im Dashboard sind eigenständig (kein Framework): Hover-Tooltip und Scroll-Zoom vorhanden, aber kein Pan/Brush wie bei TradingView.
- Die Equity-Kurve der Performance Engine ist eine einfache chronologische Summe (kein Portfolio-Kapitalmodell, kein Compounding).
- Heatmap zeigt aktuell Score/Liquidität/Preisänderung/Volumen; eine Volatilitäts-Metrik wäre erst mit N zusätzlichen Historienabfragen möglich und wurde aus Performance-Gründen nicht ergänzt.
- KI-Assistent bleibt bewusst ohne LLM (nur Retrieval/Arithmetik über gespeicherte Daten); Push-Benachrichtigungen sind nur als Architektur vorbereitet, nicht aktiv.
- **Es werden zu keinem Zeitpunkt automatische Wetten, Orders oder Wallet-Transaktionen ausgeführt.** Dieses Projekt ist ausschließlich lesend, auch das Dashboard hat keine Trading-Funktion.

## Nächster Entwicklungsschritt (Phase 5)

1. Kalshi/Metaculus-Anbindung klären (Auth-Modell, Rate Limits) und aktivieren.
2. UI zum manuellen Bestätigen/Ablehnen von Cross-Provider-Matching-Kandidaten (`status: candidate → confirmed/rejected`).
3. Für Signale mit echter Prognosewahrscheinlichkeit (`forecast_probability`) Kalibrierung sichtbar machen; Portfolio-Simulation mit echtem Kapitalmodell statt einfacher Summenkurve.
4. Deployment/Auth erst nach expliziter Freigabe — weiterhin lokal, weiterhin ohne Trading-Funktion.
