from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .models import Market
from .signals import PreviousSnapshot

# A Shadow-Setup only ever appears when several *independent* factors line
# up at once — this is the deliberate "ignore 90% of markets" filter. Each
# factor below contributes both points and a German, human-readable reason;
# nothing here is expressed as a raw "Signal 17" or a bare number to the user.
MIN_CONFIRMING_FACTORS = 2
MIN_SHADOW_SCORE = 55.0


@dataclass(frozen=True)
class ShadowScoreBreakdown:
    """Every component that fed into the Shadow-Score, so the number is
    never a black box. Values are points (0-100 scale contribution), not
    probabilities."""

    liquiditaet: float = 0.0
    preisbewegung: float = 0.0
    volumen: float = 0.0
    datenqualitaet: float = 0.0
    news_relevanz: float = 0.0
    historische_vergleichbarkeit: float = 0.0
    cross_provider_abweichung: float = 0.0
    bestaetigungen: float = 0.0

    def as_dict(self) -> dict:
        return {
            "liquiditaet": self.liquiditaet,
            "preisbewegung": self.preisbewegung,
            "volumen": self.volumen,
            "datenqualitaet": self.datenqualitaet,
            "news_relevanz": self.news_relevanz,
            "historische_vergleichbarkeit": self.historische_vergleichbarkeit,
            "cross_provider_abweichung": self.cross_provider_abweichung,
            "bestaetigungen": self.bestaetigungen,
        }

    @property
    def total(self) -> float:
        raw = (
            self.liquiditaet
            + self.preisbewegung
            + self.volumen
            + self.datenqualitaet
            + self.news_relevanz
            + self.historische_vergleichbarkeit
            + self.cross_provider_abweichung
            + self.bestaetigungen
        )
        return round(max(0.0, min(100.0, raw)), 1)


@dataclass(frozen=True)
class ShadowSetup:
    """A fully explained, German-language research highlight. This is what
    the user actually sees — never a raw signal type or score alone."""

    market: Market
    score: float
    breakdown: ShadowScoreBreakdown
    warum_interessant: tuple[str, ...] = field(default_factory=tuple)
    warum_nicht: tuple[str, ...] = field(default_factory=tuple)
    was_fehlt: tuple[str, ...] = field(default_factory=tuple)
    confirming_factor_count: int = 0

    @property
    def qualifies(self) -> bool:
        return self.confirming_factor_count >= MIN_CONFIRMING_FACTORS and self.score >= MIN_SHADOW_SCORE

    def as_dict(self) -> dict:
        return {
            "provider": self.market.provider,
            "provider_market_id": self.market.provider_market_id,
            "score": self.score,
            "breakdown": self.breakdown.as_dict(),
            "warum_interessant": list(self.warum_interessant),
            "warum_nicht": list(self.warum_nicht),
            "was_fehlt": list(self.was_fehlt),
            "confirming_factor_count": self.confirming_factor_count,
        }


def evaluate_shadow_setup(
    market: Market,
    previous: PreviousSnapshot | None = None,
    data_quality_score: float | None = None,
    news_count: int = 0,
    news_max_confidence: float | None = None,
    comparable_market_count: int = 0,
    cross_provider_divergence: float | None = None,
    now: datetime | None = None,
) -> ShadowSetup:
    """Combines several *independent* observations into one transparent
    Shadow-Score. A market only becomes a real Shadow-Setup when at least
    `MIN_CONFIRMING_FACTORS` of these fire together — a single unusual
    volume spike alone is not enough. This function never estimates a win
    probability; it only measures how much attention the research is worth.
    """
    now = now or datetime.now(UTC)
    warum_interessant: list[str] = []
    warum_nicht: list[str] = []
    was_fehlt: list[str] = []
    confirming = 0

    liquiditaet_pts = 0.0
    if market.liquidity >= 100_000:
        liquiditaet_pts = 15.0
        warum_interessant.append(f"hohe Liquidität (${market.liquidity:,.0f})")
        confirming += 1
    elif market.liquidity >= 25_000:
        liquiditaet_pts = 8.0
    else:
        was_fehlt.append("geringe Liquidität – Preise könnten sich leicht verzerren lassen")

    volumen_pts = 0.0
    if previous and previous.volume_24h and market.volume_24h and previous.volume_24h > 0:
        change = (market.volume_24h - previous.volume_24h) / previous.volume_24h
        if change >= 0.5:
            volumen_pts = 15.0
            warum_interessant.append(f"ungewöhnlicher Anstieg des Handelsvolumens (+{change:.0%} seit letzter Prüfung)")
            confirming += 1
        elif change <= -0.5:
            warum_nicht.append("Handelsvolumen ist stark eingebrochen")
    else:
        was_fehlt.append("noch keine Volumenhistorie zum Vergleich vorhanden")

    preisbewegung_pts = 0.0
    if previous and previous.yes_price is not None and market.yes_price is not None:
        delta = market.yes_price - previous.yes_price
        if abs(delta) >= 0.07:
            preisbewegung_pts = 20.0
            richtung = "gestiegen" if delta > 0 else "gefallen"
            warum_interessant.append(f"ungewöhnliche Preisbewegung: YES-Preis ist um {abs(delta):.0%} {richtung}")
            confirming += 1
        elif abs(delta) >= 0.03:
            preisbewegung_pts = 8.0
    else:
        was_fehlt.append("noch kein Vorher-Preis zum Vergleich vorhanden")

    datenqualitaet_pts = 0.0
    if data_quality_score is not None:
        if data_quality_score >= 90:
            datenqualitaet_pts = 10.0
            warum_interessant.append(f"hohe Datenqualität ({data_quality_score:.0f}%)")
        elif data_quality_score < 60:
            warum_nicht.append(f"eingeschränkte Datenqualität ({data_quality_score:.0f}%) – Vorsicht bei der Einordnung")
    else:
        was_fehlt.append("noch keine Datenqualitätsprüfung vorhanden")

    news_pts = 0.0
    if news_count > 0:
        news_pts = min(15.0, 5.0 + news_count * 3.0)
        warum_interessant.append(
            f"{news_count} verknüpfte Nachricht(en)"
            + (f", höchste Relevanz {news_max_confidence:.0%}" if news_max_confidence else "")
        )
        confirming += 1
    else:
        was_fehlt.append("keine verknüpften Nachrichten gefunden")

    historisch_pts = 0.0
    if comparable_market_count > 0:
        historisch_pts = min(10.0, comparable_market_count * 3.0)
        warum_interessant.append(f"{comparable_market_count} vergleichbare(r) historische(r) Fall/Fälle gefunden")
        confirming += 1
    else:
        was_fehlt.append("keine vergleichbaren historischen Fälle gefunden")

    cross_pts = 0.0
    if cross_provider_divergence is not None:
        if cross_provider_divergence >= 0.05:
            cross_pts = 15.0
            warum_interessant.append(
                f"Preisabweichung von {cross_provider_divergence:.0%} gegenüber einem vergleichbaren Markt auf einer anderen Plattform"
            )
            confirming += 1
    else:
        was_fehlt.append("kein bestätigter Vergleichsmarkt auf einer anderen Plattform")

    bestaetigungen_pts = min(15.0, confirming * 5.0)

    breakdown = ShadowScoreBreakdown(
        liquiditaet=liquiditaet_pts,
        preisbewegung=preisbewegung_pts,
        volumen=volumen_pts,
        datenqualitaet=datenqualitaet_pts,
        news_relevanz=news_pts,
        historische_vergleichbarkeit=historisch_pts,
        cross_provider_abweichung=cross_pts,
        bestaetigungen=bestaetigungen_pts,
    )

    if not warum_interessant:
        warum_interessant.append("bisher keine auffälligen Faktoren erkannt")

    return ShadowSetup(
        market=market,
        score=breakdown.total,
        breakdown=breakdown,
        warum_interessant=tuple(warum_interessant),
        warum_nicht=tuple(warum_nicht),
        was_fehlt=tuple(was_fehlt),
        confirming_factor_count=confirming,
    )
