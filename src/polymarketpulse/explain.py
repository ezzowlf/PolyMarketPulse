from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Explanation:
    """A structured, fully-sourced answer. `statements` are the only claims
    made; `evidence` lists exactly the DB rows each statement is built from,
    so every sentence traces back to stored data. No generative model is
    involved — this is retrieval and arithmetic over SQLite, nothing else."""

    question: str
    statements: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"question": self.question, "statements": self.statements, "evidence": self.evidence}


def explain_market_movement(conn: sqlite3.Connection, market_id: str) -> Explanation:
    """Answers: "Why did this market move?" using only stored snapshots,
    signals and linked news for this exact market_id."""
    market = conn.execute(
        "SELECT question, provider, provider_market_id FROM markets WHERE market_id = ?", (market_id,)
    ).fetchone()
    if market is None:
        return Explanation(question="Warum bewegt sich dieser Markt?", statements=["Markt nicht gefunden."])

    question_text, provider, provider_market_id = market
    statements: list[str] = []
    evidence: list[dict] = []

    snapshots = conn.execute(
        "SELECT captured_at, yes_price FROM market_snapshots WHERE market_id = ? "
        "ORDER BY captured_at ASC",
        (market_id,),
    ).fetchall()
    priced = [(t, p) for t, p in snapshots if p is not None]
    if len(priced) >= 2:
        change = priced[-1][1] - priced[0][1]
        statements.append(
            f"YES-Preis änderte sich von {priced[0][1]:.1%} ({priced[0][0]}) auf "
            f"{priced[-1][1]:.1%} ({priced[-1][0]}), Differenz {change:+.1%}."
        )
        evidence.append({"type": "price_history", "rows": len(priced)})
    else:
        statements.append("Nicht genug Preis-Snapshots für eine Bewegungsanalyse vorhanden.")

    signals = conn.execute(
        """
        SELECT captured_at, signal_type, score, reasons FROM research_signals
        WHERE provider = ? AND provider_market_id = ? ORDER BY captured_at ASC
        """,
        (provider, provider_market_id),
    ).fetchall()
    if signals:
        types = ", ".join(sorted({s[1] for s in signals}))
        statements.append(f"{len(signals)} Research-Signal(e) im beobachteten Zeitraum, Typen: {types}.")
        evidence.append({"type": "research_signals", "rows": [dict(zip(("captured_at", "signal_type", "score", "reasons"), s, strict=True)) for s in signals]})
    else:
        statements.append("Keine Research-Signale für diesen Markt gespeichert.")

    news = conn.execute(
        """
        SELECT n.title, n.published_at, l.confidence FROM news_market_links l
        JOIN news_events n ON n.id = l.news_event_id
        WHERE l.provider = ? AND l.provider_market_id = ? ORDER BY n.published_at ASC
        """,
        (provider, provider_market_id),
    ).fetchall()
    if news:
        statements.append(f"{len(news)} verknüpfte News-Meldung(en) gefunden.")
        evidence.append({"type": "news", "rows": [dict(zip(("title", "published_at", "confidence"), n, strict=True)) for n in news]})
    else:
        statements.append("Keine verknüpften News-Meldungen für diesen Markt gespeichert.")

    return Explanation(question=f"Warum bewegt sich '{question_text}'?", statements=statements, evidence=evidence)


def relevant_news_for_market(conn: sqlite3.Connection, market_id: str) -> Explanation:
    market = conn.execute(
        "SELECT provider, provider_market_id FROM markets WHERE market_id = ?", (market_id,)
    ).fetchone()
    if market is None:
        return Explanation(question="Welche News waren relevant?", statements=["Markt nicht gefunden."])
    provider, provider_market_id = market
    rows = conn.execute(
        """
        SELECT n.title, n.source, n.published_at, l.confidence, l.matched_terms
        FROM news_market_links l JOIN news_events n ON n.id = l.news_event_id
        WHERE l.provider = ? AND l.provider_market_id = ? ORDER BY l.confidence DESC
        """,
        (provider, provider_market_id),
    ).fetchall()
    if not rows:
        return Explanation(question="Welche News waren relevant?", statements=["Keine verknüpften News gefunden."])
    statements = [
        f"'{r[0]}' ({r[1]}, {r[2]}) — Confidence {r[3]:.0%}, gemeinsame Begriffe: {r[4]}" for r in rows
    ]
    return Explanation(
        question="Welche News waren relevant?",
        statements=statements,
        evidence=[{"type": "news_market_links", "rows": len(rows)}],
    )


def signals_before_movement(conn: sqlite3.Connection, market_id: str) -> Explanation:
    market = conn.execute(
        "SELECT provider, provider_market_id FROM markets WHERE market_id = ?", (market_id,)
    ).fetchone()
    if market is None:
        return Explanation(question="Welche Signale lagen vorher vor?", statements=["Markt nicht gefunden."])
    provider, provider_market_id = market
    rows = conn.execute(
        """
        SELECT captured_at, signal_type, score FROM research_signals
        WHERE provider = ? AND provider_market_id = ? ORDER BY captured_at ASC
        """,
        (provider, provider_market_id),
    ).fetchall()
    statements = [f"{r[0]}: {r[1]} (Score {r[2]:.1f})" for r in rows] or ["Keine vorherigen Signale gespeichert."]
    return Explanation(
        question="Welche Signale lagen vorher vor?", statements=statements,
        evidence=[{"type": "research_signals", "rows": len(rows)}],
    )


def similar_markets(conn: sqlite3.Connection, market_id: str, limit: int = 5) -> Explanation:
    """Finds historically similar markets using the same word-overlap
    similarity already used for cross-provider matching — applied here
    within a single provider's market set instead."""
    from .matching import text_similarity

    target = conn.execute("SELECT question FROM markets WHERE market_id = ?", (market_id,)).fetchone()
    if target is None:
        return Explanation(question="Welche historischen Märkte waren vergleichbar?", statements=["Markt nicht gefunden."])
    (target_question,) = target

    candidates = conn.execute(
        "SELECT market_id, question FROM markets WHERE market_id != ? "
        "AND resolution_status IN ('resolved','cancelled','invalid') LIMIT 500",
        (market_id,),
    ).fetchall()
    scored = sorted(
        ((mid, q, text_similarity(target_question, q)) for mid, q in candidates),
        key=lambda t: t[2],
        reverse=True,
    )
    top = [t for t in scored if t[2] > 0][:limit]
    if not top:
        return Explanation(
            question="Welche historischen Märkte waren vergleichbar?",
            statements=["Keine ausreichend ähnlichen, bereits aufgelösten Märkte gefunden."],
        )
    statements = [f"{q} (Ähnlichkeit {sim:.0%})" for _mid, q, sim in top]
    return Explanation(
        question="Welche historischen Märkte waren vergleichbar?",
        statements=statements,
        evidence=[{"type": "text_similarity", "rows": len(top)}],
    )
