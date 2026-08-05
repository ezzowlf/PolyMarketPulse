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
- **Prognose-Engine** (`prediction.py`): transparente, base-rate-blendende statistische Prognose (kein ML-Blackbox-Modell) — berechnet Markt-/Modellwahrscheinlichkeit, Edge, Konfidenz, Datenqualität und Empfehlung, bevor irgendeine KI aufgerufen wird.
- **AI-Erklärungsschicht** (`src/polymarketpulse/ai/`): kontrollierter GPT-5-nano-Research-Assistent, der die Prognose-Engine nur *erklärt*, nie selbst Wahrscheinlichkeiten erfindet — siehe eigener Abschnitt unten. Standardmäßig deaktiviert.
- **Backtest-Engine** (`backtest.py`): zeitbasierter Walk-Forward-Backtest der Prognose-Engine (kein Look-ahead), Brier Score, Log Loss, Kalibrierung, simulierte Performance/Drawdown, Aufschlüsselung nach Richtung und Kategorie.
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
python -m polymarketpulse predict <market_id>
python -m polymarketpulse explain-recommendation <market_id> [--no-cache]
python -m polymarketpulse cost-report --days 7
python -m polymarketpulse backtest [--category <name>] [--min-train-size 5]
python -m polymarketpulse evaluation
```

(Falls das Skript `polymarketpulse` nach der Installation nicht im PATH gefunden wird, funktioniert alternativ `python -m polymarketpulse.cli <command>`.)

## Dashboard + REST API starten

```powershell
python -m polymarketpulse serve
```

Öffnet die API auf `http://127.0.0.1:8000` (Swagger unter `/docs`) und liefert das Dashboard unter `/` aus. Das Dashboard liest **ausschließlich aus der lokalen SQLite-Datenbank** — es fragt bei jedem Seitenaufruf keine Provider live ab. Neue Daten kommen ausschließlich über `polymarketpulse scan` (CLI oder als geplanter Task).

Seiten: Dashboard, Märkte, Marktdetail (Charts, historische Analyse, Signalhistorie, News, **eigene Prognose & KI-Erklärung**), Watchlist, Chancen (Signale), Research (datenbasierte Erklärungen), News, Kalender, Resolutionen, Simulation, Performance, Statistik, Analytics, **Backtest & KI-Kosten**, Data Quality, Provider-Vergleich (Cross-Provider-Preisdivergenz), Provider (Capability-Dashboard), Scanner-Monitoring, Heatmap, Suche, Einstellungen.

### REST-Endpunkte

`GET /health`, `/providers`, `/provider/{name}`, `/providers/status`, `/markets`, `/market/{id}`, `/signals`, `/signal/{id}`, `/stats`, `/news`, `/history/{market_id}`, `/history/full/{market_id}`, `/watchlist`, `/calendar`, `/heatmap`, `/analytics`, `/settings`, `/quality`, `/performance`, `/simulation`, `/resolutions`, `/search`, `/compare`, `/explain/{market_id}`, `/prediction/{market_id}`, `/ai/explain-recommendation/{market_id}`, `/ai/cost-report`, `/backtest`, `/evaluation` · `POST /watchlist`, `/ai/explain-recommendation/{market_id}/recompute` · `DELETE /watchlist/{id}`. Alle Antworten sind JSON, keine HTML-Ausgabe über die API. Keine Order-, Wallet- oder Tradingendpunkte (per Test abgesichert).

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

## Prognose-Engine V2 & GPT-5 nano-Erklärungsschicht

Diese Trennung ist die zentrale, unveränderte Architekturregel seit Phase 7: **die eigene Engine berechnet,
GPT-5 nano erklärt nur.** Version 2 ersetzt den einzelnen Basisraten-Blend aus Phase 7 durch ein Ensemble
unabhängiger, jeweils eigenständig testbarer Teilmodelle (`src/polymarketpulse/prediction/` — vormals eine
einzelne Datei `prediction.py`, jetzt ein Package). `compute_prediction()` (in `prediction/engine.py`) bleibt
das gleiche, öffentliche Einstiegssignatur wie in V1 — bestehende Aufrufer (`ai/service.py`, `backtest.py`)
mussten nicht geändert werden.

### 1. Prognose-Engine V2 — bindend, unveränderbar für die KI

Der Orchestrator berechnet, bevor irgendeine KI aufgerufen wird, die vollständigen, bindenden Werte:
`market_yes_probability`, `market_no_probability`, `estimated_yes_probability`, `estimated_no_probability`,
`gross_yes_edge`, `net_yes_edge`, `confidence_score`, `data_quality_score` (sechsteilige Aufschlüsselung),
`uncertainty_lower`/`uncertainty_upper`, `recommendation` — plus neu in V2: `deadline_phase`,
`submodel_estimates` (volle Transparenz über jedes Teilmodell), `ensemble_agreement`, `scenarios`
(Base/Bull/Bear), `news_sentiment_score`, `news_confirmation_count`.

**Module** (jedes eigenständig unit-getestet, `tests/test_prediction_v2.py`):

- **`deadline.py` — Deadline Engine.** Klassifiziert die verbleibende Zeit bis zur Resolution in 7 Phasen
  (`MORE_THAN_7_DAYS` … `FINAL_MINUTES`) und liefert dafür konfigurierte Gewichte: je näher die Deadline, desto
  höher das Gewicht für News/Momentum und desto niedriger das Gewicht der (langsam reagierenden) historischen
  Basisrate — plus eine empfohlene Scan-Frequenz je Phase (nur eine Empfehlung; die tatsächliche Scan-Cadence
  bleibt Sache der Scanner-Konfiguration).
- **`momentum.py` — Momentum/Markt-Modell.** Baut auf den bestehenden, transparenten Kennzahlen aus
  `price_analytics.py` auf (gleitende Durchschnitte, Volatilität, Trendwechsel). Der aktuelle Marktpreis ist
  immer der Anker; Momentum-Fortsetzung und Mean-Reversion wirken nur als klein gedeckelte Anpassung (± 5
  Prozentpunkte) darauf, sofern genug Preis-Historie vorliegt (≥ 3 Snapshots) — sonst wird der reine Marktpreis
  unverändert übernommen, statt das Signal komplett zu verwerfen.
- **`history.py` — Historisches Modell.** Die aus Phase 7 bekannte Basisraten-Logik, jetzt als eigenständiges
  Ensemble-Mitglied mit eigenem, stichprobengrößen-abhängigem Gewicht (gedeckelt bei 60 %).
- **`news.py` — News-Modell (nicht-LLM, deterministisch).** Bewusst **kein** Sprachmodell: ein kleines,
  auditierbares Lexikon (positiv/negativ, DE+EN) bewertet bereits verknüpfte, gespeicherte News-Titel
  (`news_events`/`news_market_links`, gefüllt von `news/` — hier wird nicht live nachgeladen). Zusätzlich fließen
  Quellvertrauen (feste Tabelle je Domain, unbekannt = neutral 0.5), Aktualität (Exponential-Decay, Halbwertszeit
  48h) und die Anzahl unabhängiger, gleichgerichtet bestätigender Quellen ein.
- **`bayesian.py` — Bayesianisches Update.** Faltet die gewichtete News-Stimmung per Log-Odds-Update in die
  Prior-Schätzung (History+Momentum-Ensemble) ein — inkrementell, keine komplette Neuberechnung. Ohne
  Nachrichtenevidenz ist das Update ein No-Op (Posterior = Prior). Verschiebung ist hart gedeckelt
  (`MAX_LOG_ODDS_SHIFT`), damit einzelne Schlagzeilen die Prognose nicht dominieren können.
- **`confidence.py` — Konfidenz, strikt getrennt von der Wahrscheinlichkeit.** Aus Datenqualität (35 %),
  Anzahl verfügbarer Teilmodelle (25 %), Modellübereinstimmung/Ensemble-Agreement (25 %) und Marktstabilität
  (15 %). Ein Score von 80 ist niemals automatisch eine Wahrscheinlichkeit von 80 % — dieselbe Regel wie im
  System-Prompt für GPT-5 nano.
- **`scenarios.py` — Szenario-Engine (nicht-LLM).** Erzeugt Base-/Bull-/Bear-Case ausschließlich aus bereits
  berechneten, strukturierten Fakten (Teilmodell-Schätzungen, positive/negative News, historische Basisrate) über
  feste Textbausteine — keine KI ist an der Entscheidung beteiligt, *was* die Szenarien aussagen; GPT-5 nano
  bekommt dieses fertige Set später nur zur sprachlichen Ausformulierung.
- **`ensemble.py` — Meta-Modell.** Transparent gewichteter Durchschnitt der verfügbaren Teilmodelle (nicht
  verfügbare Teilmodelle — z. B. History ohne genug Vergleichsfälle — werden ausgeschlossen, nie stillschweigend
  auf 0.5 gesetzt).
- **`engine.py` — Orchestrator.** Verdrahtet alle Module in der Reihenfolge Deadline → History+Momentum-Ensemble
  (Prior) → News+Bayesianisches Update (Posterior) → Konfidenz → Empfehlung → Szenarien, exakt wie in Punkt 4
  der ursprünglichen Auftragsvorgabe beschrieben.

Empfehlungsschwellen sind unverändert aus Phase 7 übernommen (`NO_BET` < 3pp Netto-Edge, `WATCH_*` < 8pp, sonst
`YES`/`NO`, `STRONG_*` ≥ 18pp; unter 5 Vergleichsfällen immer `INSUFFICIENT_DATA`; unter Konfidenz 40 immer
`NO_BET`) — dokumentiert in `prediction/engine.py`, nicht kalibriert auf einen bestimmten Datensatz.

### 2. Performance Tracking V2 (`evaluation.py`)

Jede berechnete Prognose wird unabhängig von einem KI-Aufruf dauerhaft gespeichert (`prediction_snapshots`,
Migration 8) — `get_prediction()` allein löst das bereits aus. `evaluate_predictions()` verknüpft diese
Snapshots nach Marktauflösung mit `market_resolutions` und berechnet: Accuracy/Precision/Recall (nur über
YES/NO-Empfehlungen, `NO_BET`/`INSUFFICIENT_DATA` ausgeschlossen), Brier Score, Log Loss, Kalibrierung, Ø
Netto-Edge und simulierten ROI einer festen-Einsatz-Strategie, die jeder YES/NO-Empfehlung folgt (nie ein echter
Trade). Unterschied zu `backtest.py`: der Backtest simuliert rückwirkend mit striktem Zeit-Split ("wie hätte
das Modell historisch performt"), `evaluation.py` wertet aus, was die Engine tatsächlich im Betrieb
vorhergesagt hat. Abrufbar über `GET /evaluation`, CLI `evaluation`, Dashboard-Seite „Backtest & KI-Kosten“.

### 2. GPT-5 nano — erklärt, erfindet nicht

Verbindliches Standardmodell: **`gpt-5-nano`** (`OPENAI_MODEL`), Fallback **`gpt-5-mini`** (`OPENAI_FALLBACK_MODEL`) —
nur bei zweimal ungültiger/inkonsistenter Nano-Antwort, weiterhin unter demselben Kostenlimit. `gpt-4.1-mini`
bleibt als separates, günstigeres Modell **nur** für den allgemeinen Research-Assistenten (Abschnitt unten)
verfügbar, wird für die Prognose-Erklärung nicht mehr verwendet.

Der 12-Schritte-Ablauf (`ai/service.py::explain_recommendation`): Markt laden → externe Datenquellen prüfen →
historische Vergleichsdaten laden → Datenqualität prüfen → Prognose berechnen (`compute_prediction`) → Markt-
vs. Eigenprognose vergleichen → Kosten/Spread/Unsicherheit einrechnen → Empfehlung bestimmen (alles Schritte 1–8,
**vor** jedem KI-Aufruf) → GPT-5 nano **nur zur Erklärung** aufrufen → JSON gegen Schema validieren → gegen die
mathematische Prognose validieren (`ai/validation.py`) → Analyse + Kosten speichern → im Dashboard anzeigen.

`ai/validation.py::validate_explanation` verwirft die KI-Antwort komplett (ein Reparaturversuch, danach
regelbasierter Fallback), wenn `direction`/`recommendation` nicht zur Engine passen, eine der vier
Wahrscheinlichkeits-/Edge-Zahlen um mehr als 1 Prozentpunkt abweicht, oder eine `source_id` zitiert wird, die
nicht in `allowed_source_ids` steht. Der Systemprompt (`ai/prompts.py::EXPLANATION_SYSTEM_PROMPT`) verbietet
zusätzlich explizit das Erfinden von Wahrscheinlichkeiten, das Ändern der Empfehlung und die Gleichsetzung eines
Scores mit einer Wahrscheinlichkeit ("Ein Score von 80 ist niemals automatisch eine Wahrscheinlichkeit von 80 %").

### 3. Kostenkontrolle

- `.env`: `OPENAI_MAX_COST_PER_ANALYSIS_USD=0.01`, `OPENAI_MAX_INPUT_TOKENS=10000`, `OPENAI_MAX_OUTPUT_TOKENS=1500`, `OPENAI_DAILY_BUDGET_USD=1.00`.
- Vor jedem Aufruf: Zeichen-basierte Token-Schätzung → Kostenschätzung (`ai/cost.py`, Preistabelle für `gpt-5-nano`/`gpt-5-mini`/`gpt-4.1-mini`) → nur senden, wenn unter dem Limit; sonst regelbasierter Fallback, kein automatisches Kürzen auf Kosten der Aussagekraft.
- Tagesbudget wird per SQL-Summe über `ai_analysis_runs.actual_cost_usd` seit Mitternacht geprüft (`within_daily_budget`).
- Nach jedem echten Aufruf werden die tatsächlichen Token-Zahlen aus der Antwort gespeichert (`input_tokens`, `output_tokens`, `estimated_cost_usd`, `actual_cost_usd`) — keine geschätzten Werte werden nachträglich als „echt“ ausgegeben.
- Sichtbar über `GET /ai/cost-report`, CLI `cost-report`, Dashboard-Seite „Backtest & KI-Kosten“.

### 4. Fallback ohne KI

`ai/fallback.py::build_fallback_explanation` erzeugt aus den reinen `PredictionResult`-Werten eine vollständige,
deutschsprachige Erklärung — deterministisch, ohne jeden API-Aufruf. Ausgelöst bei: AI deaktiviert/kein Key,
Eingabe über Token-Limit, geschätzte Kosten über dem Limit, Tagesbudget erreicht, GPT zweimal ungültig/inkonsistent.
Das Dashboard zeigt **niemals** einen leeren Zustand, solange die Prognose existiert.

### 5. Caching

Cache-Schlüssel (`hash_payload`) aus Markt-ID, Prognose-Version, Daten-Snapshot-Version, Empfehlung,
Quellen-Hash, Prompt-Version und Modell — ändert sich einer dieser Werte (z. B. neuer Marktpreis-Snapshot),
ist der Cache-Eintrag ungültig und es wird neu bewertet (bei aktivierter KI ggf. mit neuem Aufruf). `--no-cache`
(CLI) bzw. `POST .../recompute` (API/Dashboard-Button) erzwingen eine Neuberechnung unabhängig vom Cache-Alter.

### 6. Backtest

`backtest.py::run_backtest` — Walk-Forward über alle aufgelösten Märkte, chronologisch sortiert: für den
n-ten Fall werden ausschließlich die zuvor aufgelösten Fälle derselben Kategorie zur Bildung der Basisrate
verwendet (kein Look-ahead-Bias, per Test verifiziert: `tests/test_backtest.py::test_backtest_never_uses_future_resolutions`).
Metriken: Brier Score, Log Loss, Kalibrierungstabelle (vorhergesagt vs. beobachtet je Dezil), simulierte
kumulierte Rendite und Max-Drawdown, getrennt nach YES-/NO-Empfehlungen und nach Kategorie. Abrufbar über
`GET /backtest`, CLI `backtest`, Dashboard-Seite „Backtest & KI-Kosten“.

## Allgemeiner KI-Research-Assistent (GPT-4.1 mini, optional)

Ein **kontrollierter** Research-Assistent auf Basis von OpenAI GPT-4.1 mini (`src/polymarketpulse/ai/`) für freie Fragen, Markt-/Signal-/News-Einordnung und Marktvergleiche — unabhängig von der Prognose-Erklärung oben. Die KI hat **keinen** Zugriff auf SQLite, Dateien, Shell oder das Internet — sie bekommt ausschließlich einen begrenzten, vom Backend zusammengestellten JSON-Kontext (`context_builder.py`) und antwortet strikt im vorgegebenen JSON-Schema (OpenAI Structured Outputs). Standardmäßig **deaktiviert**; ohne `OPENAI_API_KEY` läuft die gesamte Anwendung unverändert weiter.

### Einrichtung

1. Bei OpenAI ein API-Projekt anlegen und Abrechnung/Limits dort konfigurieren (außerhalb dieses Repos — PolymarketPulse rechnet nichts fest ein und berechnet keine Kosten selbst).
2. In `.env` (niemals committen, siehe `.gitignore`):
   ```env
   POLYMARKETPULSE_AI_ENABLED=true
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-5-nano
   OPENAI_FALLBACK_MODEL=gpt-5-mini
   OPENAI_TIMEOUT_SECONDS=30
   OPENAI_MAX_OUTPUT_TOKENS=1500
   OPENAI_MAX_INPUT_TOKENS=10000
   OPENAI_MAX_COST_PER_ANALYSIS_USD=0.01
   OPENAI_DAILY_BUDGET_USD=1.00
   POLYMARKETPULSE_AI_CACHE_TTL_SECONDS=900
   ```
   `OPENAI_MODEL`/`OPENAI_FALLBACK_MODEL` gelten für die Prognose-Erklärung (Abschnitt oben); der allgemeine
   Research-Assistent unten kann unabhängig davon mit `gpt-4.1-mini` konfiguriert bleiben, wenn gewünscht.
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

- **`category` ist derzeit kein normalisiertes Themenfeld.** In der produktiven Datenbank wird `markets.category` faktisch mit dem (meist eindeutigen) Marktfragetext befüllt statt mit einer echten Taxonomie (z. B. "Esports", "Politik US"). Dadurch findet die Basisraten-Abfrage der Prognose-Engine für die meisten realen Märkte aktuell **keine** ≥5 vergleichbaren aufgelösten Fälle, und die Empfehlung fällt auf `INSUFFICIENT_DATA` zurück — dokumentiert und reproduzierbar in `analysis/reports/phase7_acceptance_examples.json` (Beispiel D). Eine normalisierte Kategorisierung ist der wirkungsvollste nächste Schritt, um die Prognose-Engine auf echten Produktionsdaten nutzbar zu machen.
- Die simulierte Backtest-Rendite und der `performance.py`-Equity-Verlauf sind vereinfachte Modelle (fester Einsatz je Trade, kein Portfolio-/Kapitalmodell, kein Compounding) — keine reale Handelsperformance.
- `backtest.py` verwendet weiterhin die einfachere V1-Basisraten-Blend-Formel, nicht das volle V2-Ensemble (Deadline/Momentum/News/Bayesianisches Update) — eine vollständige V2-Rückrechnung bräuchte historische Preis-Snapshot- und News-Daten *zum jeweiligen Zeitpunkt* jedes Altfalls, die aktuell nicht in dieser Tiefe gespeichert sind. `evaluation.py` (Performance Tracking V2) bewertet dagegen bereits die echten V2-Prognosen im Betrieb.
- Das News-Sentiment-Lexikon (`prediction/news.py`) ist bewusst klein und regelbasiert (auditierbar, kein NLP-Modell) — Ironie, Verneinung ("nicht bestätigt") und komplexe Satzstrukturen werden nicht erkannt; die Quellvertrauens-Tabelle deckt nur eine Handvoll bekannter Domains ab, alles andere fällt auf neutrales Vertrauen (0.5) zurück.
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
