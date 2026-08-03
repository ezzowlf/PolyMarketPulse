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
- **AI-Schicht** (`src/polymarketpulse/ai/`): kontrollierter GPT-4.1-mini-Research-Assistent — siehe eigener Abschnitt unten. Standardmäßig deaktiviert.
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

## KI-Research-Assistent (GPT-4.1 mini, optional)

Ein **kontrollierter** Research-Assistent auf Basis von OpenAI GPT-4.1 mini (`src/polymarketpulse/ai/`). Die KI hat **keinen** Zugriff auf SQLite, Dateien, Shell oder das Internet — sie bekommt ausschließlich einen begrenzten, vom Backend zusammengestellten JSON-Kontext (`context_builder.py`) und antwortet strikt im vorgegebenen JSON-Schema (OpenAI Structured Outputs). Standardmäßig **deaktiviert**; ohne `OPENAI_API_KEY` läuft die gesamte Anwendung unverändert weiter.

### Einrichtung

1. Bei OpenAI ein API-Projekt anlegen und Abrechnung/Limits dort konfigurieren (außerhalb dieses Repos — PolymarketPulse rechnet nichts fest ein und berechnet keine Kosten selbst).
2. In `.env` (niemals committen, siehe `.gitignore`):
   ```env
   POLYMARKETPULSE_AI_ENABLED=true
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-4.1-mini
   OPENAI_TIMEOUT_SECONDS=30
   OPENAI_MAX_OUTPUT_TOKENS=1200
   POLYMARKETPULSE_AI_CACHE_TTL_SECONDS=900
   ```
3. Ohne `POLYMARKETPULSE_AI_ENABLED=true` **und** einen gesetzten Key bleiben alle `/ai/*`-Endpunkte auf `503`, die CLI-`ai-*`-Befehle brechen kontrolliert ab, das Dashboard zeigt einen klaren „AI nicht verfügbar“-Zustand — nirgendwo ein Absturz oder ein automatischer Aufruf.

### Funktionen

- `explain_market(market_id)` — warum bewegt sich der Markt, Pro-/Contra-Faktoren, Datenlücken.
- `explain_signal(signal_id)` — ordnet ein Research-Signal ein.
- `analyze_news_for_market(market_id)` — bewertet nur bereits gespeicherte, verknüpfte News.
- `compare_markets(market_ids)` — nur wenn mindestens ein **bestätigter** (`status='confirmed'`) Cross-Provider-Match zwischen den Märkten existiert.
- `ask_research_question(question, market_id=None)` — freie Frage, ausschließlich auf Basis des optionalen Marktkontexts.

Jede Antwort folgt dem festen Schema `AnalysisResult` (Zusammenfassung, Pro-/Contra-Faktoren mit Beleg und Stärke, relevante News, Datenlücken, Unsicherheiten, Quellen-IDs, `confidence_in_analysis` als Kontextabdeckung — **keine Gewinnwahrscheinlichkeit** — und ein fixer Disclaimer).

### Prompt-Regeln / Prompt-Injection-Schutz

Der System-Prompt (`ai/prompts.py`) verbietet erfundene Fakten, Handlungsanweisungen („jetzt kaufen“), garantierte Gewinne und die Behandlung des Research-Scores als Wahrscheinlichkeit — und weist das Modell explizit an, jede Anweisung zu ignorieren, die innerhalb von Markttexten oder News-Inhalten steht. Getestet mit gezielt präparierten Markt-/News-Texten, die Prompt-Injection versuchen (`tests/test_ai_service.py`).

### API-Endpunkte

`GET /ai/status` · `POST /ai/explain-market/{market_id}` · `POST /ai/explain-signal/{signal_id}` · `POST /ai/analyze-news/{market_id}` · `POST /ai/compare` · `POST /ai/ask`. Fehlerbehandlung: `503` wenn AI deaktiviert/ohne Key, `424` bei unzureichendem Kontext (z.B. unbekannter Markt, keine News, kein bestätigter Match), `429`/`504`/`502` für Rate-Limit/Timeout/Netzwerkfehler — nie eine rohe OpenAI-Fehlermeldung oder ein Secret in der Antwort.

### CLI

```powershell
python -m polymarketpulse ai-status
python -m polymarketpulse ai-explain-market <market-id>
python -m polymarketpulse ai-explain-signal <signal-id>
python -m polymarketpulse ai-ask "Warum bewegte sich dieser Markt?" --market-id <id>
```

### Manueller Live-Smoke-Test (echter, kostenpflichtiger OpenAI-Aufruf)

```powershell
python -m polymarketpulse ai-smoke-test --market-id <id>
```

Läuft **ausschließlich manuell** und nur, wenn `POLYMARKETPULSE_AI_ENABLED=true` **und** ein echter `OPENAI_API_KEY` gesetzt sind — niemals Teil der normalen Testsuite, niemals automatisch.

### Caching und Kostenkontrolle

- Cache-Schlüssel aus Analyseart + Modell + Prompt-Version + Kontext-Hash (`ai/cache.py`, `ai/context_builder.py`); identische Anfragen innerhalb des TTL (`POLYMARKETPULSE_AI_CACHE_TTL_SECONDS`, Standard 900s) lösen **keinen** neuen OpenAI-Aufruf aus.
- Kontext ist hart begrenzt: max. 20 Preis-Snapshots, max. 8 Research-Signale, max. 5 News, max. 5 Vergleichsmärkte, Beschreibungstext auf 800 Zeichen gekürzt — niemals ein vollständiger Tabellen-Dump.
- Tokenverbrauch (`input_tokens`/`output_tokens`, sofern vom SDK geliefert) wird pro Lauf gespeichert; keine fest codierte Preisrechnung, da OpenAI-Preise sich ändern können.

### Datenbank: `ai_analysis_runs`

Neue Tabelle (Migration 5) dient gleichzeitig als Ausführungslog und Cache-Speicher: `id`, `analysis_type`, `market_id`, `model`, `prompt_version`, `context_hash`, `status`, `created_at`, `duration_ms`, `input_tokens`, `output_tokens`, `cached`, `error_code`, `response_json`. **Kein** API-Key, **kein** roher Prompt-Text wird gespeichert — nur die strukturierte JSON-Antwort und Metadaten (siehe `tests/test_ai_no_secrets.py`).

### Datenschutz

Logs/DB-Zeilen enthalten Analyse-ID, Modell, Dauer, Status, Tokenverbrauch, Kontext-Hash — niemals API-Key, Authorization-Header, vollständige Rohprompts oder Secrets. Ungeprüfte KI-Antworten werden nicht als Fakten in andere Tabellen übernommen, sondern bleiben isoliert in `ai_analysis_runs`.

## Bekannte Einschränkungen

- Kalshi und Metaculus sind noch nicht live angebunden (siehe Tabelle oben).
- PredictIt liefert keine Volumen-/Liquiditätsdaten und keinen separaten Resolution-Feed.
- `CROSS_PROVIDER_DIVERGENCE` und Cross-Provider-Matching liefern nur `status='candidate'` — nichts wird automatisch als identisch bestätigt; es gibt noch keine UI zum manuellen Bestätigen/Ablehnen.
- News-Matching und Reaktionsmessung sind einfache, auditierbare Heuristiken (Begriffsabgleich, Preisfenster-Vergleich), keine echte NLP-Entitätserkennung oder Kausalanalyse.
- Charts im Dashboard sind eigenständig (kein Framework): Hover-Tooltip und Scroll-Zoom vorhanden, aber kein Pan/Brush wie bei TradingView.
- Die Equity-Kurve der Performance Engine ist eine einfache chronologische Summe (kein Portfolio-Kapitalmodell, kein Compounding).
- Heatmap zeigt aktuell Score/Liquidität/Preisänderung/Volumen; eine Volatilitäts-Metrik wäre erst mit N zusätzlichen Historienabfragen möglich und wurde aus Performance-Gründen nicht ergänzt.
- Der retrieval-only `explain.py` bleibt bewusst ohne LLM; GPT-4.1 mini ist als separater, optionaler AI-Layer daneben verfügbar (siehe oben) — beide koexistieren auf der Research-Seite.
- Push-Benachrichtigungen sind nur als Architektur vorbereitet, nicht aktiv.
- Der `compare`-AI-Endpunkt setzt einen bereits `confirmed` Cross-Provider-Match voraus; es gibt noch keine UI, um Matches zu bestätigen (siehe Cross-Provider-Matching-Einschränkung oben) — `ai/compare` ist daher aktuell nur nutzbar, wenn ein Match direkt in der DB auf `confirmed` gesetzt wurde.
- Kein Rate-Limiting auf API-Ebene implementiert (nur Fehlercode-Vorbereitung für `429`); für einen Mehrbenutzerbetrieb wäre ein echtes Request-Throttling nötig.
- **Es werden zu keinem Zeitpunkt automatische Wetten, Orders oder Wallet-Transaktionen ausgeführt.** Dieses Projekt ist ausschließlich lesend, auch das Dashboard hat keine Trading-Funktion.

## Nächster Entwicklungsschritt (Phase 6)

1. Kalshi/Metaculus-Anbindung klären (Auth-Modell, Rate Limits) und aktivieren.
2. UI zum manuellen Bestätigen/Ablehnen von Cross-Provider-Matching-Kandidaten (`status: candidate → confirmed/rejected`) — würde auch `ai/compare` nutzbarer machen.
3. Für Signale mit echter Prognosewahrscheinlichkeit (`forecast_probability`) Kalibrierung sichtbar machen; Portfolio-Simulation mit echtem Kapitalmodell statt einfacher Summenkurve.
4. Echtes Rate-Limiting für `/ai/*` und die übrige API; strukturiertes Logging statt `print`.
4. Deployment/Auth erst nach expliziter Freigabe — weiterhin lokal, weiterhin ohne Trading-Funktion.
