from __future__ import annotations

import httpx

from .models import Market, Signal

DISCLAIMER = (
    "Research-Hinweis – keine Wettaufforderung, kein sicherer Gewinn. "
    "Eine faire Wahrscheinlichkeit muss separat geschätzt und kalibriert werden."
)


def format_signal(signal: Signal) -> str:
    market = signal.market
    yes = "–" if market.yes_price is None else f"{market.yes_price:.1%}"
    spread = "–" if market.spread is None else f"{market.spread:.1%}"
    reasons = ", ".join(signal.reasons) or "Datenlage beobachten"
    return (
        f"🔎 {signal.signal_type} [{market.provider}] – {DISCLAIMER}\n\n"
        f"{market.question}\n\n"
        f"YES: {yes}\n"
        f"Research-Score: {signal.score:.0f}/100\n"
        f"Liquidität: ${market.liquidity:,.0f}\n"
        f"24h-Volumen: ${market.volume_24h:,.0f}\n"
        f"Spread: {spread}\n"
        f"Begründung: {reasons}\n\n"
        f"{market.url}"
    )


def format_resolution(market: Market) -> str:
    outcome = market.winning_outcome or "unbekannt"
    return (
        f"✅ MARKT AUFGELÖST [{market.provider}] – {DISCLAIMER}\n\n"
        f"{market.question}\n\n"
        f"Ergebnis: {outcome}\n"
        f"Auflösungszeitpunkt: {market.resolved_at.isoformat() if market.resolved_at else '–'}\n"
        f"Quelle: {market.resolution_source or '–'}\n\n"
        f"{market.url}"
    )


def format_daily_stats(stats: dict) -> str:
    lines = [f"📊 TAGESSTATISTIK – {DISCLAIMER}", ""]
    for key, value in stats.items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def format_provider_outage(provider: str, error: str) -> str:
    return (
        f"⚠️ PROVIDER-AUSFALL [{provider}] – Research-Hinweis, keine Handlungsaufforderung\n\n"
        f"Der Abruf für '{provider}' ist fehlgeschlagen: {error}\n"
        "Automatische Wiederholung erfolgt beim nächsten geplanten Scan."
    )


def send_message(token: str, chat_id: str, text: str) -> None:
    """Send a single Telegram message. Never call this unless the caller has
    already confirmed real sending is explicitly enabled and authorized."""
    response = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=20.0,
    )
    response.raise_for_status()
