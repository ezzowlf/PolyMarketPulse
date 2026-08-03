from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
from datetime import UTC, datetime, timedelta

from .config import Settings
from .data_quality import assess_market
from .performance import compute_performance
from .providers.base import Page, ProviderError
from .providers.registry import create_provider, list_provider_names
from .signals import PreviousSnapshot, generate_signals
from .stats import compute_signal_stats
from .storage import Storage
from .telegram import (
    format_daily_stats,
    format_provider_outage,
    format_resolution,
    format_signal,
    send_message,
)

ALERT_COOLDOWN_HOURS = 24


def _ensure_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows consoles using legacy codepages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _provider_names(args: argparse.Namespace, settings: Settings) -> list[str]:
    requested = getattr(args, "provider", None) or settings.default_provider
    if requested == "all":
        return list_provider_names()
    return [requested]


def _scan_one_provider(
    provider_name: str, settings: Settings, storage: Storage, limit: int
) -> tuple[int, int, int, list]:
    """Returns (markets_read, snapshots_saved, markets_failed, top_signals)."""
    started = time.monotonic()
    provider = create_provider(provider_name, timeout=settings.request_timeout)
    storage.register_provider(provider_name, provider_name.title(), provider.capabilities)
    run_id = storage.start_run(provider_name)

    try:
        page: Page = provider.fetch_markets(limit=limit)
        markets = page.items
        batch = []
        all_signals = []
        quality_reports = []
        for market in markets:
            quality_reports.append((provider_name, assess_market(market)))
            previous = storage.get_previous_snapshot(market.provider, market.provider_market_id)
            prev_snapshot = (
                PreviousSnapshot(
                    liquidity=previous.liquidity,
                    volume_24h=previous.volume_24h,
                    spread=previous.spread,
                    yes_price=previous.yes_price,
                    one_day_change=previous.one_day_change,
                )
                if previous
                else None
            )
            market_signals = generate_signals(market, previous=prev_snapshot)
            if (
                market.liquidity >= settings.min_liquidity
                and market.volume_24h >= settings.min_volume_24h
            ):
                batch.append((market, market_signals))
                all_signals.extend(market_signals)

        snapshots_saved = storage.save(run_id, batch)
        storage.save_quality_reports(run_id, quality_reports)
        duration_ms = int((time.monotonic() - started) * 1000)
        storage.finish_run(
            run_id,
            status="completed",
            markets_read=len(markets),
            markets_saved=len(batch),
            markets_failed=0,
            duration_ms=duration_ms,
        )
        all_signals.sort(key=lambda s: s.score, reverse=True)
        return len(markets), snapshots_saved, 0, all_signals
    except ProviderError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        storage.finish_run(
            run_id,
            status="failed",
            markets_read=0,
            markets_saved=0,
            markets_failed=1,
            duration_ms=duration_ms,
            error_details=str(exc),
        )
        raise
    finally:
        provider.close()


def cmd_scan(args: argparse.Namespace) -> int:
    settings = Settings.load()
    limit = args.limit or settings.scan_limit
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    exit_code = 0
    all_top_signals = []
    try:
        for provider_name in _provider_names(args, settings):
            try:
                read, saved, _failed, top_signals = _scan_one_provider(
                    provider_name, settings, storage, limit
                )
                all_top_signals.extend(top_signals[:10])
                if not args.json:
                    print(
                        f"[{provider_name}] {read} Märkte gelesen, {saved} Snapshots gespeichert.",
                        file=sys.stderr,
                    )
            except ProviderError as exc:
                exit_code = 2
                print(f"[{provider_name}] Fehler: {exc}", file=sys.stderr)
                if args.send_alerts and settings.telegram_enabled and settings.telegram_bot_token:
                    send_message(
                        settings.telegram_bot_token,
                        settings.telegram_chat_id,
                        format_provider_outage(provider_name, str(exc)),
                    )

        all_top_signals.sort(key=lambda s: s.score, reverse=True)
        top = all_top_signals[:10]

        if args.json:
            payload = [
                {
                    "provider": s.market.provider,
                    "provider_market_id": s.market.provider_market_id,
                    "question": s.market.question,
                    "signal_type": s.signal_type,
                    "score": s.score,
                    "yes_price": s.market.yes_price,
                    "liquidity": s.market.liquidity,
                    "volume_24h": s.market.volume_24h,
                    "spread": s.market.spread,
                    "reasons": list(s.reasons),
                    "url": s.market.url,
                }
                for s in top
            ]
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for index, signal in enumerate(top, start=1):
                market = signal.market
                yes = "–" if market.yes_price is None else f"{market.yes_price:.1%}"
                print(
                    f"{index:>2}. {signal.score:>5.1f} | {market.provider:<10} | YES {yes:>6} | "
                    f"{signal.signal_type:<12} | {market.question}"
                )
                print(f"    {market.url}")

        if args.send_alerts:
            exit_code = max(exit_code, _send_alerts(settings, storage, top))

        return exit_code
    finally:
        storage.close()


def _send_alerts(settings: Settings, storage: Storage, top: list) -> int:
    if not settings.telegram_enabled:
        print(
            "Telegram-Versand ist deaktiviert (POLYMARKETPULSE_TELEGRAM_ENABLED=false). "
            "Kein Versand durchgeführt.",
            file=sys.stderr,
        )
        return 0
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("Telegram-Zugangsdaten fehlen in .env.", file=sys.stderr)
        return 1

    candidates = [s for s in top if s.score >= settings.alert_score]
    since = (datetime.now(UTC) - timedelta(hours=ALERT_COOLDOWN_HOURS)).isoformat()
    keys = [f"{s.market.provider}:{s.market.provider_market_id}" for s in candidates]
    already_alerted = storage.markets_alerted_since(keys, since)
    to_send = [
        s for s in candidates if f"{s.market.provider}:{s.market.provider_market_id}" not in already_alerted
    ]
    for signal in to_send:
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, format_signal(signal))
    storage.record_alerts([f"{s.market.provider}:{s.market.provider_market_id}" for s in to_send])
    print(
        f"{len(to_send)} Telegram-Hinweis(e) gesendet "
        f"({len(candidates) - len(to_send)} durch Cooldown übersprungen).",
        file=sys.stderr,
    )
    return 0


def cmd_markets(args: argparse.Namespace) -> int:
    settings = Settings.load()
    limit = args.limit or settings.scan_limit
    provider_name = args.provider or settings.default_provider
    try:
        with create_provider(provider_name, timeout=settings.request_timeout) as provider:
            markets = provider.fetch_markets(limit=limit).items
    except ProviderError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = [
            {
                "provider": m.provider,
                "provider_market_id": m.provider_market_id,
                "question": m.question,
                "yes_price": m.yes_price,
                "no_price": m.no_price,
                "liquidity": m.liquidity,
                "volume_24h": m.volume_24h,
                "spread": m.spread,
                "end_at": m.end_at.isoformat() if m.end_at else None,
                "url": m.url,
            }
            for m in markets
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"{len(markets)} aktive Märkte von '{provider_name}' abgerufen.\n")
        for market in markets[:20]:
            yes = "–" if market.yes_price is None else f"{market.yes_price:.1%}"
            print(f"  YES {yes:>6} | Liq ${market.liquidity:>12,.0f} | {market.question}")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    settings = Settings.load()
    rows = []
    for name in list_provider_names():
        provider = create_provider(name, timeout=settings.request_timeout)
        rows.append({"name": name, **provider.capabilities.as_dict()})
        provider.close()

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        header = f"{'Provider':<12} {'Listen':<7} {'Preise':<7} {'Buch':<6} {'Vol':<5} {'Liq':<5} {'Res':<5} {'Auth':<6} {'Echtgeld':<9}"
        print(header)
        for row in rows:
            print(
                f"{row['name']:<12} {row['market_lists']!s:<7} {row['prices']!s:<7} "
                f"{row['orderbook']!s:<6} {row['volume']!s:<5} {row['liquidity']!s:<5} "
                f"{row['resolution']!s:<5} {row['requires_auth']!s:<6} {row['real_money']!s:<9}"
            )
    return 0


def cmd_provider_info(args: argparse.Namespace) -> int:
    settings = Settings.load()
    provider = create_provider(args.name, timeout=settings.request_timeout)
    info = {"name": provider.name, **provider.capabilities.as_dict()}
    provider.close()
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
    else:
        print(f"Provider: {info['name']}")
        for key, value in info.items():
            if key == "name":
                continue
            print(f"  {key}: {value}")
    return 0


def cmd_resolutions(args: argparse.Namespace) -> int:
    settings = Settings.load()
    provider_name = args.provider or settings.default_provider
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        provider = create_provider(provider_name, timeout=settings.request_timeout)
        try:
            if not provider.capabilities.resolution:
                print(f"'{provider_name}' unterstützt kein Resolution-Tracking.", file=sys.stderr)
                return 1
            page = provider.fetch_resolved_markets(limit=args.limit or 50)
        except NotImplementedError as exc:
            print(f"Nicht implementiert: {exc}", file=sys.stderr)
            return 1
        except ProviderError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return 2
        finally:
            provider.close()

        newly_recorded = 0
        for market in page.items:
            if storage.record_resolution(market):
                newly_recorded += 1

        if args.json:
            print(
                json.dumps(
                    {"fetched": len(page.items), "newly_recorded": newly_recorded},
                    indent=2,
                )
            )
        else:
            print(f"{len(page.items)} aufgelöste Märkte abgerufen, {newly_recorded} neu erfasst.")
        return 0
    finally:
        storage.close()


def cmd_signals(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        rows = storage.connection.execute(
            """
            SELECT rs.id, rs.provider, rs.provider_market_id, rs.captured_at, rs.signal_type,
                   rs.score, rs.reasons, rs.status, m.question
            FROM research_signals rs
            LEFT JOIN markets m ON m.provider = rs.provider AND m.provider_market_id = rs.provider_market_id
            ORDER BY rs.captured_at DESC LIMIT ?
            """,
            (args.limit or 20,),
        ).fetchall()
    finally:
        storage.close()

    if args.json:
        payload = [
            {
                "id": r[0],
                "provider": r[1],
                "provider_market_id": r[2],
                "captured_at": r[3],
                "signal_type": r[4],
                "score": r[5],
                "reasons": r[6],
                "status": r[7],
                "question": r[8],
            }
            for r in rows
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(f"[{r[0]:>5}] {r[3]} | {r[1]:<10} | {r[4]:<24} | {r[5]:>5.1f} | {r[8]}")
    return 0


def cmd_signal_stats(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        stats = compute_signal_stats(storage.connection)
    finally:
        storage.close()

    if args.json:
        print(json.dumps(stats.as_dict(), indent=2, ensure_ascii=False))
    else:
        d = stats.as_dict()
        print(f"Signale gesamt:      {d['signal_count']}")
        print(f"Ausgewertet:         {d['evaluated_count']}")
        print(f"Trefferquote:        {d['hit_rate']}")
        print(f"Ø Signalpreis:       {d['average_signal_price']}")
        print(f"Ø simulierte Rendite (1 virt. Einheit): {d['average_simulated_return']}")
        print(f"Brier Score:         {d['brier_score']} (nur mit forecast_probability)")
        print(f"Log Loss:            {d['log_loss']} (nur mit forecast_probability)")
        print(f"Nach Signaltyp:      {d['breakdown_by_type']}")
        print(f"Nach Provider:       {d['breakdown_by_provider']}")
        print(f"Nach Kategorie:      {d['breakdown_by_category']}")
        print(f"Nach Liquidität:     {d['breakdown_by_liquidity']}")
        print(f"Nach Restlaufzeit:   {d['breakdown_by_time_to_resolution']}")
    return 0


def cmd_news_fetch(args: argparse.Namespace) -> int:
    settings = Settings.load()
    if not settings.news_enabled:
        print(
            "News-Modul ist deaktiviert (POLYMARKETPULSE_NEWS_ENABLED=false). "
            "Kein Abruf durchgeführt.",
            file=sys.stderr,
        )
        return 0

    from .news.linker import link_news_to_markets
    from .news.rss import fetch_all

    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        events = fetch_all(timeout=settings.request_timeout)
        new_count = 0
        saved_ids = {}
        for event in events:
            row_id = storage.save_news_event(event)
            if row_id is not None:
                new_count += 1
                saved_ids[event.source_url] = row_id

        provider = create_provider(settings.default_provider, timeout=settings.request_timeout)
        try:
            markets = provider.fetch_markets(limit=100).items
        except ProviderError as exc:
            print(f"Marktabruf für News-Zuordnung fehlgeschlagen: {exc}", file=sys.stderr)
            markets = []
        finally:
            provider.close()

        links = link_news_to_markets(events, markets)
        links_saved = 0
        for link in links:
            row_id = saved_ids.get(link.news_event.source_url)
            if row_id is not None:
                storage.save_news_market_link(row_id, link)
                links_saved += 1

        result = {"events_fetched": len(events), "events_new": new_count, "links_saved": links_saved}
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{len(events)} News abgerufen, {new_count} neu, {links_saved} Markt-Verknüpfungen.")
        return 0
    finally:
        storage.close()


def cmd_db_migrate(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        # Storage() already migrates on construction; report what happened.
        version = storage.schema_version()
        if args.json:
            print(json.dumps({"schema_version": version}, indent=2))
        else:
            print(f"Datenbank ist auf Schema-Version {version}.")
        return 0
    finally:
        storage.close()


def cmd_db_status(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        status = storage.status()
    finally:
        storage.close()

    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(f"Datenbank: {settings.database_path}")
        print(f"Schema-Version: {status['schema_version']}")
        for table in (
            "markets",
            "market_snapshots",
            "price_history",
            "scanner_runs",
            "research_signals",
            "market_resolutions",
            "signal_evaluations",
            "news_events",
            "news_market_links",
            "market_matches",
        ):
            print(f"  {table:<20} {status.get(table):>8} Zeilen")
        print(f"\nLetzter Lauf ({status['last_run_provider']}): {status['last_run_status']}")
        print(f"  gestartet: {status['last_run_started_at']}")
        print(f"  beendet:   {status['last_run_finished_at']}")
        print(f"  Märkte:    {status['last_run_markets_fetched']}")
    return 0


def cmd_telegram_preview(args: argparse.Namespace) -> int:
    settings = Settings.load()
    limit = args.limit or settings.scan_limit
    provider_name = args.provider or settings.default_provider
    try:
        with create_provider(provider_name, timeout=settings.request_timeout) as provider:
            markets = provider.fetch_markets(limit=limit).items
    except ProviderError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2

    signals = []
    for market in markets:
        if market.liquidity >= settings.min_liquidity and market.volume_24h >= settings.min_volume_24h:
            signals.extend(generate_signals(market))
    signals.sort(key=lambda s: s.score, reverse=True)
    top = signals[:3]

    if not top:
        print("Keine Märkte oberhalb der konfigurierten Schwellen gefunden.")
        return 0

    print("--- TELEGRAM PREVIEW (kein Versand) ---\n")
    for signal in top:
        print(format_signal(signal))
        print("\n" + ("-" * 40) + "\n")

    if top[0].market.resolution_status.value == "resolved":
        print(format_resolution(top[0].market))
        print()

    print(format_daily_stats({"Märkte geprüft": len(markets), "Signale": len(signals)}))
    print()
    print(format_provider_outage(provider_name, "Beispiel: Verbindungsfehler (nur Vorschau)"))
    print()
    print(
        f"Telegram-Versand ist aktuell {'AKTIVIERT' if settings.telegram_enabled else 'DEAKTIVIERT'} "
        "(POLYMARKETPULSE_TELEGRAM_ENABLED)."
    )
    return 0


def cmd_market_history(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        rows = storage.connection.execute(
            """
            SELECT captured_at, yes_price, no_price, liquidity, volume_24h, spread, opportunity_score
            FROM market_snapshots WHERE market_id = ? ORDER BY captured_at ASC
            """,
            (args.market_id,),
        ).fetchall()
    finally:
        storage.close()

    if args.json:
        print(
            json.dumps(
                [
                    dict(
                        zip(
                            (
                                "captured_at",
                                "yes_price",
                                "no_price",
                                "liquidity",
                                "volume_24h",
                                "spread",
                                "opportunity_score",
                            ),
                            row,
                            strict=True,
                        )
                    )
                    for row in rows
                ],
                indent=2,
            )
        )
    else:
        if not rows:
            print(f"Keine Historie für '{args.market_id}' gefunden.")
        for row in rows:
            print(f"{row[0]} | YES {row[1]} | Liq {row[3]} | Score {row[6]}")
    return 0 if rows or args.json else 1


def cmd_quality(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        reports = storage.latest_quality_reports(provider=args.provider)
    finally:
        storage.close()
    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        if not reports:
            print("Keine Data-Quality-Reports vorhanden. Zuerst `scan` ausführen.")
        for r in reports[: args.limit or 20]:
            print(f"{r['score']:>5.1f}% | {r['provider']:<10} | {r['question'] or r['provider_market_id']}")
            for issue in r["issues"]:
                print(f"        - {issue}")
    return 0


def cmd_performance(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        summary = compute_performance(storage.connection)
    finally:
        storage.close()
    if args.json:
        print(json.dumps(summary.as_dict(), indent=2))
    else:
        print(f"Ausgewertete Signale: {summary.evaluated_count}")
        print(f"Kumulierte simulierte Rendite (virt. Einheiten): {summary.cumulative_return}")
        print(f"Ø Rendite je Signal: {summary.average_return_per_signal}")
        print(f"Max. Drawdown: {summary.max_drawdown}")
        print(f"Trefferquote: {summary.win_rate}")
        print(f"Ø Haltedauer (h): {summary.average_hold_hours}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        results = storage.search(args.term, limit=args.limit or 20)
    finally:
        storage.close()
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for category, items in results.items():
            print(f"--- {category} ({len(items)}) ---")
            for item in items:
                print(f"  {item}")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    from .explain import (
        explain_market_movement,
        relevant_news_for_market,
        signals_before_movement,
        similar_markets,
    )

    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    handlers = {
        "movement": explain_market_movement,
        "news": relevant_news_for_market,
        "signals": signals_before_movement,
        "similar": similar_markets,
    }
    try:
        handler = handlers.get(args.mode, explain_market_movement)
        explanation = handler(storage.connection, args.market_id)
    finally:
        storage.close()
    if args.json:
        print(json.dumps(explanation.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(explanation.question)
        for statement in explanation.statements:
            print(f"  - {statement}")
    return 0


def _print_ai_error(exc: Exception) -> int:
    from .ai.client import (
        AIContextError,
        AIDisabledError,
        AINetworkError,
        AIRateLimitError,
        AIResponseError,
        AITimeoutError,
    )

    if isinstance(exc, AIDisabledError):
        print(f"AI nicht verfügbar: {exc}", file=sys.stderr)
        return 3
    if isinstance(exc, AIContextError):
        print(f"Zu wenig Kontext: {exc}", file=sys.stderr)
        return 4
    if isinstance(exc, (AITimeoutError, AIRateLimitError, AINetworkError, AIResponseError)):
        print(f"AI-Fehler: {exc}", file=sys.stderr)
        return 5
    raise exc


def cmd_ai_status(args: argparse.Namespace) -> int:
    settings = Settings.load()
    payload = {
        "enabled": settings.ai_enabled,
        "ready": settings.ai_ready,
        "model": settings.openai_model,
        "cache_ttl_seconds": settings.ai_cache_ttl_seconds,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"AI aktiviert: {settings.ai_enabled}")
        print(f"AI einsatzbereit (Key vorhanden): {settings.ai_ready}")
        print(f"Modell: {settings.openai_model}")
        print(f"Cache-TTL: {settings.ai_cache_ttl_seconds}s")
    return 0


def _print_ai_result(response, as_json: bool) -> None:
    if as_json:
        print(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))
        return
    result = response.result
    print(f"Zusammenfassung: {result.summary}")
    print(f"Erklärung: {result.market_move_explanation}")
    if result.supporting_factors:
        print("Pro-Faktoren:")
        for f in result.supporting_factors:
            print(f"  + [{f.strength}] {f.factor} — {f.evidence}")
    if result.opposing_factors:
        print("Contra-Faktoren:")
        for f in result.opposing_factors:
            print(f"  - [{f.strength}] {f.factor} — {f.evidence}")
    if result.data_gaps:
        print("Datenlücken:", ", ".join(result.data_gaps))
    if result.uncertainties:
        print("Unsicherheiten:", ", ".join(result.uncertainties))
    print(f"Confidence (Kontextabdeckung): {result.confidence_in_analysis}")
    print(f"{result.disclaimer}")
    print(f"[Modell: {response.meta.model} | Cache: {response.meta.cached} | Analyse-ID: {response.meta.analysis_id}]")


def cmd_ai_explain_market(args: argparse.Namespace) -> int:
    from .ai import service as ai_service
    from .ai.client import AIError

    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        response = ai_service.explain_market(storage, settings, args.market_id)
    except AIError as exc:
        return _print_ai_error(exc)
    finally:
        storage.close()
    _print_ai_result(response, args.json)
    return 0


def cmd_ai_explain_signal(args: argparse.Namespace) -> int:
    from .ai import service as ai_service
    from .ai.client import AIError

    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        response = ai_service.explain_signal(storage, settings, args.signal_id)
    except AIError as exc:
        return _print_ai_error(exc)
    finally:
        storage.close()
    _print_ai_result(response, args.json)
    return 0


def cmd_ai_ask(args: argparse.Namespace) -> int:
    from .ai import service as ai_service
    from .ai.client import AIError

    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        response = ai_service.ask_research_question(storage, settings, args.question, args.market_id)
    except AIError as exc:
        return _print_ai_error(exc)
    finally:
        storage.close()
    _print_ai_result(response, args.json)
    return 0


def cmd_ai_smoke_test(args: argparse.Namespace) -> int:
    """Manual-only, real OpenAI call. Refuses to run unless AI is explicitly
    enabled AND an API key is configured — never runs as part of the normal
    test suite or any automated flow."""
    from .ai import service as ai_service
    from .ai.client import AIError

    settings = Settings.load()
    if not settings.ai_ready:
        print(
            "Smoke-Test abgebrochen: POLYMARKETPULSE_AI_ENABLED=true und OPENAI_API_KEY "
            "müssen gesetzt sein, um einen echten OpenAI-Aufruf durchzuführen.",
            file=sys.stderr,
        )
        return 1
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        print(f"Führe echten OpenAI-Aufruf für Markt '{args.market_id}' aus (Modell: {settings.openai_model})…", file=sys.stderr)
        response = ai_service.explain_market(storage, settings, args.market_id)
    except AIError as exc:
        return _print_ai_error(exc)
    finally:
        storage.close()
    _print_ai_result(response, args.json)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the REST API + dashboard (read-only) via uvicorn. The CLI
    remains fully independent of this — `serve` is purely additive."""
    try:
        import uvicorn
    except ImportError:
        print("uvicorn ist nicht installiert. `pip install -e \".[dev]\"` ausführen.", file=sys.stderr)
        return 1
    print(f"Dashboard/API auf http://{args.host}:{args.port} (Strg+C zum Beenden)", file=sys.stderr)
    uvicorn.run("polymarketpulse.api:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_export_signals(args: argparse.Namespace) -> int:
    settings = Settings.load()
    storage = Storage(settings.database_path, store_unchanged_snapshots=settings.store_unchanged_snapshots)
    try:
        rows = storage.connection.execute(
            """
            SELECT id, provider, provider_market_id, captured_at, signal_type, score, reasons, status
            FROM research_signals ORDER BY captured_at DESC LIMIT ?
            """,
            (args.limit or 1000,),
        ).fetchall()
    finally:
        storage.close()

    columns = ("id", "provider", "provider_market_id", "captured_at", "signal_type", "score", "reasons", "status")
    if args.format == "json":
        print(json.dumps([dict(zip(columns, row, strict=True)) for row in rows], indent=2, ensure_ascii=False))
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        writer.writerows(rows)
        print(buffer.getvalue())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only prediction-market research scanner")
    parser.add_argument("--send-alerts", action="store_true", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Märkte abrufen, bewerten und speichern")
    scan_parser.add_argument("--provider", default=None, help="Provider-Name oder 'all'")
    scan_parser.add_argument("--limit", type=int, default=None)
    scan_parser.add_argument("--json", action="store_true")
    scan_parser.add_argument("--send-alerts", action="store_true")
    scan_parser.set_defaults(func=cmd_scan)

    markets_parser = subparsers.add_parser("markets", help="Nur aktive Märkte auflisten, ohne zu speichern")
    markets_parser.add_argument("--provider", default=None)
    markets_parser.add_argument("--limit", type=int, default=None)
    markets_parser.add_argument("--json", action="store_true")
    markets_parser.set_defaults(func=cmd_markets)

    providers_parser = subparsers.add_parser("providers", help="Verfügbare Provider und Capabilities")
    providers_parser.add_argument("--json", action="store_true")
    providers_parser.set_defaults(func=cmd_providers)

    provider_info_parser = subparsers.add_parser("provider-info", help="Capabilities eines Providers")
    provider_info_parser.add_argument("name")
    provider_info_parser.add_argument("--json", action="store_true")
    provider_info_parser.set_defaults(func=cmd_provider_info)

    provider_caps_parser = subparsers.add_parser(
        "provider-capabilities", help="Alias für `providers --json`"
    )
    provider_caps_parser.set_defaults(func=lambda a: cmd_providers(argparse.Namespace(json=True)))

    resolutions_parser = subparsers.add_parser("resolutions", help="Aufgelöste Märkte abrufen und erfassen")
    resolutions_parser.add_argument("--provider", default=None)
    resolutions_parser.add_argument("--limit", type=int, default=None)
    resolutions_parser.add_argument("--json", action="store_true")
    resolutions_parser.set_defaults(func=cmd_resolutions)

    signals_parser = subparsers.add_parser("signals", help="Zuletzt erzeugte Research-Signale anzeigen")
    signals_parser.add_argument("--limit", type=int, default=20)
    signals_parser.add_argument("--json", action="store_true")
    signals_parser.set_defaults(func=cmd_signals)

    signal_stats_parser = subparsers.add_parser("signal-stats", help="Statistische Auswertung aufgelöster Signale")
    signal_stats_parser.add_argument("--json", action="store_true")
    signal_stats_parser.set_defaults(func=cmd_signal_stats)

    news_parser = subparsers.add_parser("news-fetch", help="News-Feeds abrufen und Märkten zuordnen")
    news_parser.add_argument("--json", action="store_true")
    news_parser.set_defaults(func=cmd_news_fetch)

    db_migrate_parser = subparsers.add_parser("db-migrate", help="Datenbankmigrationen ausführen")
    db_migrate_parser.add_argument("--json", action="store_true")
    db_migrate_parser.set_defaults(func=cmd_db_migrate)

    db_parser = subparsers.add_parser("db-status", help="Datenbankstatus anzeigen")
    db_parser.add_argument("--json", action="store_true")
    db_parser.set_defaults(func=cmd_db_status)

    preview_parser = subparsers.add_parser(
        "telegram-preview", help="Telegram-Nachrichten anzeigen, ohne sie zu senden"
    )
    preview_parser.add_argument("--provider", default=None)
    preview_parser.add_argument("--limit", type=int, default=None)
    preview_parser.set_defaults(func=cmd_telegram_preview)

    history_parser = subparsers.add_parser("market-history", help="Preis-/Score-Historie eines Marktes")
    history_parser.add_argument("market_id")
    history_parser.add_argument("--json", action="store_true")
    history_parser.set_defaults(func=cmd_market_history)

    export_parser = subparsers.add_parser("export-signals", help="Research-Signale exportieren")
    export_parser.add_argument("--format", choices=["csv", "json"], default="csv")
    export_parser.add_argument("--limit", type=int, default=None)
    export_parser.set_defaults(func=cmd_export_signals)

    quality_parser = subparsers.add_parser("quality", help="Data-Quality-Reports anzeigen")
    quality_parser.add_argument("--provider", default=None)
    quality_parser.add_argument("--limit", type=int, default=None)
    quality_parser.add_argument("--json", action="store_true")
    quality_parser.set_defaults(func=cmd_quality)

    performance_parser = subparsers.add_parser("performance", help="Simulierte Performance über aufgelöste Signale")
    performance_parser.add_argument("--json", action="store_true")
    performance_parser.set_defaults(func=cmd_performance)

    search_parser = subparsers.add_parser("search", help="Globale Suche über Märkte/News/Signale/Resolutionen")
    search_parser.add_argument("term")
    search_parser.add_argument("--limit", type=int, default=None)
    search_parser.add_argument("--json", action="store_true")
    search_parser.set_defaults(func=cmd_search)

    explain_parser = subparsers.add_parser("explain", help="Datenbasierte Erklärung zu einem Markt")
    explain_parser.add_argument("market_id")
    explain_parser.add_argument(
        "--mode", choices=["movement", "news", "signals", "similar"], default="movement"
    )
    explain_parser.add_argument("--json", action="store_true")
    explain_parser.set_defaults(func=cmd_explain)

    ai_status_parser = subparsers.add_parser("ai-status", help="AI-Konfigurationsstatus anzeigen")
    ai_status_parser.add_argument("--json", action="store_true")
    ai_status_parser.set_defaults(func=cmd_ai_status)

    ai_explain_market_parser = subparsers.add_parser(
        "ai-explain-market", help="KI-Analyse eines Marktes (nur wenn AI aktiviert)"
    )
    ai_explain_market_parser.add_argument("market_id")
    ai_explain_market_parser.add_argument("--json", action="store_true")
    ai_explain_market_parser.set_defaults(func=cmd_ai_explain_market)

    ai_explain_signal_parser = subparsers.add_parser(
        "ai-explain-signal", help="KI-Analyse eines Research-Signals (nur wenn AI aktiviert)"
    )
    ai_explain_signal_parser.add_argument("signal_id", type=int)
    ai_explain_signal_parser.add_argument("--json", action="store_true")
    ai_explain_signal_parser.set_defaults(func=cmd_ai_explain_signal)

    ai_ask_parser = subparsers.add_parser("ai-ask", help="Research-Frage an die KI stellen (nur wenn AI aktiviert)")
    ai_ask_parser.add_argument("question")
    ai_ask_parser.add_argument("--market-id", dest="market_id", default=None)
    ai_ask_parser.add_argument("--json", action="store_true")
    ai_ask_parser.set_defaults(func=cmd_ai_ask)

    ai_smoke_parser = subparsers.add_parser(
        "ai-smoke-test",
        help="Echter, manueller OpenAI-Aufruf (nur mit POLYMARKETPULSE_AI_ENABLED=true + Key)",
    )
    ai_smoke_parser.add_argument("--market-id", dest="market_id", required=True)
    ai_smoke_parser.add_argument("--json", action="store_true")
    ai_smoke_parser.set_defaults(func=cmd_ai_smoke_test)

    serve_parser = subparsers.add_parser("serve", help="REST API + Dashboard starten (nur lesend)")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        args.command = "scan"
        args.func = cmd_scan
        args.provider = None
        args.limit = None
        args.json = False

    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
