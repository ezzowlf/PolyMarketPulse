"""Market category / event-type taxonomy — Phase C.

Replaces reliance on Polymarket's raw `events[0].title` string (an
uncontrolled, provider-specific label like "Politics" or "2024 Election"
that can be missing, inconsistent, or simply absent for other providers)
with a deterministic, auditable classifier over a **fixed** category set
that every provider's markets can be mapped onto.

Design goals (mirrors the rest of the prediction/ package):
  * No LLM calls, fully rule-based, deterministic, unit-testable.
  * Not a single-keyword classifier. Each category is scored from several
    independent signal groups (weighted phrase groups, known entity/ticker
    lists, structural cues), and the highest-scoring category wins.
  * `semantics.py`'s `event_type` (when a `MarketProposition` is supplied)
    is treated as a strong signal — often decisive on its own — because it
    already encodes real understanding of what the market is asserting,
    not just which words appear in the title.
  * Genuine ambiguity (two categories scoring close together) is reported
    as *lower confidence*, not silently resolved with false certainty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .semantics import MarketProposition

# ---------------------------------------------------------------------------
# Fixed category taxonomy. Do not add/remove without updating every caller
# that switches on these values (dashboards, history.py grouping, etc.).
# ---------------------------------------------------------------------------

CATEGORIES = (
    "POLITICS", "ELECTIONS", "GEOPOLITICS", "WAR_PEACE", "LEGISLATION",
    "MACROECONOMICS", "CENTRAL_BANKS", "CRYPTO", "FINANCIAL_MARKETS",
    "ENERGY", "TECHNOLOGY", "SPORT_FOOTBALL", "SPORT_BASKETBALL",
    "SPORT_TENNIS", "SPORT_OTHER", "ENTERTAINMENT", "SOCIAL", "OTHER",
)

_WORD_RE = re.compile(r"[A-Za-z0-9'\$]+")


def _lower(text: str | None) -> str:
    return (text or "").lower()


# ---------------------------------------------------------------------------
# Weighted keyword/phrase groups per category. Each group is a tuple of
# phrases that all contribute the SAME weight if found (a group models one
# "idea", e.g. "resignation-flavoured politics language"); a category's
# score is the sum of weights of every group that had at least one hit,
# which is what makes this a multi-signal classifier rather than a
# single-keyword one.
# ---------------------------------------------------------------------------

# (weight, phrases)
_KEYWORD_GROUPS: dict[str, tuple[tuple[float, tuple[str, ...]], ...]] = {
    "ELECTIONS": (
        # NB: bare "primary" is deliberately excluded — Polymarket's
        # boilerplate resolution-source text routinely contains phrases
        # like "the primary resolution source", which would otherwise
        # false-positive on completely unrelated markets.
        (2.0, ("election", "elections", "presidential primary", "primary election", "general election",
                "midterm", "midterms")),
        (1.5, ("nominee", "nomination", "electoral college", "ballot", "runoff", "run-off")),
        (1.0, ("popular vote", "swing state", "poll leader", "win the presidency", "win the election")),
    ),
    "POLITICS": (
        (2.0, ("president", "governor", "senator", "congress", "cabinet")),
        (1.5, ("resign", "resignation", "impeach", "impeachment", "approval rating", "out as president",
                "removed from office", "step down", "steps down")),
        (1.0, ("white house", "administration", "political party", "vote of no confidence")),
    ),
    "GEOPOLITICS": (
        # "prime minister" / "chancellor" / "parliament" live here (not in
        # POLITICS) because in this taxonomy POLITICS is scoped to
        # domestic/US-style politics and GEOPOLITICS to foreign-state and
        # international contexts — a named foreign head-of-government
        # title is one of the strongest single cues for that split.
        (2.5, ("prime minister", "chancellor", "parliament")),
        (2.0, ("nato", "united nations", "un security council", "sanctions", "diplomatic", "summit",
                "bilateral", "foreign minister", "embassy")),
        (1.5, ("annex", "annexation", "territorial dispute", "border dispute", "coup", "regime change")),
        (1.0, ("trade deal", "trade war", "tariff", "tariffs")),
    ),
    "WAR_PEACE": (
        (2.5, ("ceasefire", "cease-fire", "truce", "peace deal", "peace talks", "armistice",
                "war", "invasion", "offensive", "military strike", "airstrike", "shelling")),
        (1.5, ("troops", "mobilization", "mobilize", "conflict", "hostilities", "combat")),
        (1.0, ("withdrawal of troops", "peacekeeping")),
    ),
    "LEGISLATION": (
        (2.5, ("bill", "legislation", "law passed", "pass the bill", "signed into law", "veto",
                "senate vote", "house vote", "congressional vote")),
        (1.5, ("supreme court", "ruling", "legal challenge", "enacted", "statute")),
        (1.0, ("committee vote", "filibuster")),
    ),
    "MACROECONOMICS": (
        (2.0, ("gdp", "inflation", "cpi", "unemployment rate", "jobs report", "recession",
                "consumer price index", "nonfarm payrolls")),
        (1.5, ("economic growth", "trade deficit", "economy contracts", "economy grows")),
        (1.0, ("stimulus", "fiscal policy")),
    ),
    "CENTRAL_BANKS": (
        (3.0, ("federal reserve", "the fed", "fomc", "european central bank", "ecb", "bank of england",
                "boe", "bank of japan", "boj", "jerome powell", "rate decision", "rate cut", "rate hike",
                "interest rate", "basis points")),
        (1.0, ("monetary policy", "quantitative easing", "quantitative tightening")),
    ),
    "CRYPTO": (
        (3.0, ("bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "solana", "sol",
                "dogecoin", "doge", "xrp", "stablecoin", "defi", "binance", "coinbase")),
        (1.0, ("blockchain", "nft", "altcoin", "halving")),
    ),
    "FINANCIAL_MARKETS": (
        (2.5, ("s&p 500", "s&p500", "nasdaq", "dow jones", "stock market", "stock price", "share price",
                "ipo", "index fund", "bond yield", "treasury yield")),
        (1.5, ("bull market", "bear market", "market close above", "market close below", "all-time high")),
        (1.0, ("earnings report", "quarterly earnings")),
    ),
    "ENERGY": (
        (2.5, ("oil price", "crude oil", "wti", "brent", "opec", "natural gas price", "gas price",
                "barrel")),
        (1.5, ("energy prices", "power grid", "renewable energy", "solar capacity", "nuclear plant")),
    ),
    "TECHNOLOGY": (
        (2.5, ("artificial intelligence", "openai", "chatgpt", "gpt-", "google", "apple", "microsoft",
                "meta platforms", "spacex", "tesla model", "self-driving", "quantum computing")),
        (1.5, ("app launch", "product launch", "software release", "chip shortage", "semiconductor")),
    ),
    "SPORT_FOOTBALL": (
        (3.0, ("premier league", "champions league", "world cup", "fifa", "uefa", "la liga",
                "bundesliga", "serie a", "super bowl", "nfl", "ballon d'or")),
        (1.5, ("goal", "goals", "match winner", "relegated", "relegation")),
    ),
    "SPORT_BASKETBALL": (
        (3.0, ("nba", "nba finals", "ncaa tournament", "march madness", "wnba", "euroleague")),
        (1.0, ("slam dunk", "three-pointer")),
    ),
    "SPORT_TENNIS": (
        (3.0, ("wimbledon", "us open tennis", "french open", "roland garros", "australian open",
                "atp tour", "wta tour", "grand slam")),
    ),
    "SPORT_OTHER": (
        (2.5, ("olympics", "olympic games", "formula 1", "f1 grand prix", "ufc", "boxing match",
                "cricket world cup", "rugby world cup", "golf major", "masters tournament", "nhl", "mlb")),
        # Esports leagues/titles — a large, recurring slice of real
        # Polymarket volume with no dedicated category in the fixed
        # taxonomy; bucketed under SPORT_OTHER rather than left OTHER.
        (2.0, ("league of legends", "dota 2", "csgo", "counter-strike", "valorant", "lck", "lpl",
                "lec", "esports")),
        (1.0, ("championship", "tournament winner")),
    ),
    "ENTERTAINMENT": (
        (2.5, ("box office", "movie", "film", "oscar", "oscars", "academy award", "grammy", "grammys",
                "album release", "tv series", "season finale", "streaming premiere", "celebrity")),
        (1.0, ("emmy", "emmys", "billboard chart")),
    ),
    "SOCIAL": (
        (2.5, ("twitter", "x.com", "tweet", "viral", "social media", "instagram", "tiktok", "reddit",
                "follower count", "trending topic")),
        (1.0, ("influencer",)),
    ),
}

# Entity/ticker lookup tables — checked as whole-word matches, contribute a
# strong, near-decisive score to their category (used to break ties the
# generic keyword groups above leave ambiguous, e.g. a bare "$BTC").
_CENTRAL_BANK_NAMES = (
    "federal reserve", "fomc", "european central bank", "bank of england",
    "bank of japan", "people's bank of china", "reserve bank of india",
    "swiss national bank", "bank of canada",
)
_CRYPTO_TICKERS = ("btc", "eth", "sol", "doge", "xrp", "ada", "bnb", "avax", "matic", "ltc")
_EQUITY_INDEX_NAMES = ("s&p 500", "s&p500", "nasdaq", "dow jones", "russell 2000", "ftse 100")
_SPORT_LEAGUES = {
    "SPORT_FOOTBALL": ("premier league", "champions league", "la liga", "bundesliga", "serie a",
                        "fifa world cup", "uefa"),
    "SPORT_BASKETBALL": ("nba", "wnba", "euroleague", "ncaa tournament"),
    "SPORT_TENNIS": ("wimbledon", "atp tour", "wta tour", "roland garros", "australian open"),
}

# event_type (from semantics.py) -> category, when the event_type alone is a
# near-decisive signal regardless of surface keywords. Extended (E9) to
# cover the fuller event_type vocabulary semantics.py's _detect_event_type
# can now produce (see semantics.py's E9 comments for the full rationale);
# extending semantics.py's vocabulary further should extend this table too.
_EVENT_TYPE_CATEGORY: dict[str, tuple[str, float]] = {
    # An office-departure proposition is POLITICS-shaped almost regardless
    # of phrasing; if the subject is a foreign head of state the caller may
    # prefer GEOPOLITICS, but POLITICS is the defensible default and is
    # documented as a boundary call below.
    "office_departure": ("POLITICS", 3.0),
    "war_escalation": ("WAR_PEACE", 3.0),
    "ceasefire": ("WAR_PEACE", 3.0),
    "military_action": ("WAR_PEACE", 3.0),
    "sanctions": ("WAR_PEACE", 2.5),
    "territorial_control": ("WAR_PEACE", 3.0),
    "strategic_waterway": ("WAR_PEACE", 2.5),
    "diplomatic_agreement": ("WAR_PEACE", 2.5),
    "legislation": ("LEGISLATION", 3.0),
    "election": ("ELECTIONS", 3.0),
    "appointment": ("POLITICS", 2.5),
    "court_outcome": ("POLITICS", 2.5),
    "rate_cut": ("CENTRAL_BANKS", 3.0),
    "rate_hike": ("CENTRAL_BANKS", 3.0),
    "rate_hold": ("CENTRAL_BANKS", 3.0),
    "sport_match": ("SPORT_OTHER", 2.5),
    "sport_tournament": ("SPORT_OTHER", 2.5),
    "sport_winner": ("SPORT_OTHER", 2.5),
    "sport_qualification": ("SPORT_OTHER", 2.5),
}

# Geopolitics override: if the office_departure subject/text also strongly
# suggests a foreign-state / international context, prefer GEOPOLITICS over
# POLITICS. Kept narrow and explicit rather than guessed.
_FOREIGN_HEAD_OF_STATE_TERMS = (
    "prime minister", "chancellor", "president of", "foreign leader", "regime",
)


@dataclass(frozen=True)
class MarketClassification:
    category: str
    event_type: str | None
    confidence: float
    signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "category": self.category, "event_type": self.event_type,
            "confidence": self.confidence, "signals": list(self.signals),
        }


def _contains_phrase(text: str, phrase: str) -> bool:
    """Word-boundary phrase match. Plain substring matching would let short
    tokens false-positive inside unrelated words (e.g. the ticker/keyword
    "nfl" matching inside "inflation", or "eth" matching inside "method") —
    \\b on both ends of the (possibly multi-word) phrase avoids that while
    still matching phrases containing spaces/hyphens/punctuation."""
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _score_keywords(text: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for category, groups in _KEYWORD_GROUPS.items():
        total = 0.0
        matched: list[str] = []
        for weight, phrases in groups:
            for phrase in phrases:
                if _contains_phrase(text, phrase):
                    total += weight
                    matched.append(phrase)
                    break  # one hit per group is enough to award that group's weight
        if total:
            scores[category] = total
            hits[category] = matched
    return scores, hits


def _structural_signals(text: str, scores: dict[str, float], signals: list[str]) -> None:
    """Price-threshold / entity-driven structural cues that keyword groups
    alone would get wrong (e.g. "price" appearing for a non-financial
    market, or a bare ticker with no other context)."""
    for name in _CENTRAL_BANK_NAMES:
        if _contains_phrase(text, name):
            scores["CENTRAL_BANKS"] = scores.get("CENTRAL_BANKS", 0.0) + 3.0
            signals.append(f"central_bank_entity:{name}")

    for ticker in _CRYPTO_TICKERS:
        if re.search(rf"\${ticker}\b", text) or _contains_phrase(text, ticker):
            scores["CRYPTO"] = scores.get("CRYPTO", 0.0) + 2.0
            signals.append(f"crypto_ticker:{ticker}")
            break

    for index_name in _EQUITY_INDEX_NAMES:
        if _contains_phrase(text, index_name):
            scores["FINANCIAL_MARKETS"] = scores.get("FINANCIAL_MARKETS", 0.0) + 2.5
            signals.append(f"equity_index_entity:{index_name}")

    for category, leagues in _SPORT_LEAGUES.items():
        for league in leagues:
            if _contains_phrase(text, league):
                scores[category] = scores.get(category, 0.0) + 3.0
                signals.append(f"sport_league_entity:{league}")

    # "LoL:" esports-listing prefix (Polymarket's League of Legends match
    # markets are consistently titled "LoL: <Team> vs <Team> ...") — a
    # word-boundary-safe alternative to the "lol:" phrase, since the plain
    # phrase-matcher can't reliably bound a match ending in punctuation.
    if re.search(r"\blol\s*:", text):
        scores["SPORT_OTHER"] = scores.get("SPORT_OTHER", 0.0) + 2.0
        signals.append("esports_prefix:LoL:")

    # "price" / "reach $X" structural cue: only meaningful once combined
    # with an asset signal already found above — on its own it says nothing
    # (a market can ask about "the price of eggs" and that's not finance).
    has_price_threshold = bool(re.search(r"(reach|hit|above|below|over|under)\s+\$?\d", text))
    if has_price_threshold:
        if scores.get("CRYPTO", 0.0) > 0:
            scores["CRYPTO"] += 1.0
            signals.append("price_threshold+crypto_asset")
        elif scores.get("FINANCIAL_MARKETS", 0.0) > 0:
            scores["FINANCIAL_MARKETS"] += 1.0
            signals.append("price_threshold+equity_asset")


def classify_market(
    question: str,
    resolution_text: str | None = None,
    proposition: MarketProposition | None = None,
) -> MarketClassification:
    """Classify a market's question (+ optional resolution text / parsed
    proposition) into the fixed taxonomy in `CATEGORIES`.

    Not a single-keyword lookup: combines (a) weighted keyword/phrase group
    scores per category, (b) known entity/ticker/league lookups, (c)
    structural price-threshold cues gated on an already-found asset signal,
    and (d) `proposition.event_type`, which — when available — is treated
    as one of the strongest signals available (it already reflects real
    understanding of the market's predicate, not just surface wording).
    """
    text = _lower(f"{question} {resolution_text or ''}")
    signals: list[str] = []

    scores, hits = _score_keywords(text)
    for category, matched in hits.items():
        signals.append(f"keywords[{category}]={matched}")

    _structural_signals(text, scores, signals)

    event_type = proposition.event_type if proposition else None
    if event_type and event_type in _EVENT_TYPE_CATEGORY:
        et_category, et_weight = _EVENT_TYPE_CATEGORY[event_type]
        # office_departure: prefer GEOPOLITICS over POLITICS when the text
        # strongly reads as a foreign-state context. Documented boundary
        # call — see module docstring / test suite.
        if event_type == "office_departure" and any(t in text for t in _FOREIGN_HEAD_OF_STATE_TERMS):
            et_category = "GEOPOLITICS"
        scores[et_category] = scores.get(et_category, 0.0) + et_weight
        signals.append(f"event_type[{event_type}]->{et_category} (+{et_weight})")

    if not scores:
        return MarketClassification(
            category="OTHER", event_type=event_type, confidence=0.2,
            signals=["no_keyword_or_event_type_signal_matched"],
        )

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_category, best_score = ranked[0]
    runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0

    # Confidence model: absolute strength of the winning score, discounted
    # by how close the runner-up is (genuine ambiguity -> lower confidence,
    # never silently resolved with false certainty).
    margin = best_score - runner_up_score
    raw_confidence = min(0.95, 0.35 + 0.12 * best_score)
    if runner_up_score > 0:
        closeness_penalty = max(0.0, 0.5 - (margin / max(best_score, 1e-6)) * 0.5)
        raw_confidence = max(0.25, raw_confidence - closeness_penalty)
        if margin < max(1.0, 0.25 * best_score):
            runner_up_category = ranked[1][0]
            signals.append(
                f"ambiguous_with:{runner_up_category} (score={runner_up_score:.1f} vs "
                f"{best_category}={best_score:.1f}) — lower confidence reported, not silently resolved"
            )

    confidence = round(max(0.05, min(0.98, raw_confidence)), 3)

    return MarketClassification(
        category=best_category, event_type=event_type, confidence=confidence, signals=signals,
    )
